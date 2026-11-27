"""Restore proven protobuf fallback names in decoded capture JSON.

Unknown fields are retained and reported.  A fallback that collides with an
already named field is ambiguous and is rejected instead of silently choosing
one value.
"""
from __future__ import annotations

import re

FALLBACK = re.compile(r'^field_(\d+)$')

# field number -> name, taken from the bundled net_pb2 descriptors.
SCHEMAS = {
    'weapon': {1:'weapon_id',2:'instance_id',3:'attack',4:'damage_balance',5:'critical_ratio',6:'random_property',
               7:'wearer_id',8:'level',9:'strength_level',10:'strength_exp',11:'star',12:'inscription1',
               13:'durability',14:'property_index',15:'is_lock'},
    'armor': {1:'armor_id',2:'instance_id',3:'main_property_type',4:'main_property_val',5:'random_property',
              6:'wearer_id',7:'level',8:'strength_level',9:'strength_exp',10:'property_index',15:'is_lock'},
    'poster': {1:'poster_id',2:'instance_id',3:'wearer_id',4:'star'},
    'armorInfo': {1:'equip_type',2:'armor_id'},
    'posterInfo': {1:'poster_index',2:'poster_id'},
    'outfit': {1:'outfit_id',2:'dye_schemes'},
    'dyeScheme': {1:'scheme_index',2:'colors',3:'is_un_lock'},
    'posColor': {1:'pos',2:'red',3:'green',4:'blue'},
}


class FieldConflictError(ValueError):
    """Two source fields claim the same destination field."""


def _objects(value):
    if isinstance(value, list):
        return value
    if isinstance(value, dict):
        return list(value.values())
    return []


def restore(value, schema):
    """Rename field_N using one schema; unresolved entries are counted, not dropped."""
    if not isinstance(value, dict):
        raise FieldConflictError('Capture contains a malformed equipment object.')
    table = SCHEMAS[schema]
    restored = {}
    unresolved = 0
    for key, item in value.items():
        if key in restored:
            raise FieldConflictError('Capture contains conflicting equipment fields.')
        match = FALLBACK.match(key) if isinstance(key, str) else None
        if match:
            name = table.get(int(match.group(1)))
            if name is None:
                unresolved += 1
                restored[key] = item
                continue
            if name in restored:
                raise FieldConflictError('Capture contains conflicting equipment fields.')
            restored[name] = item
        else:
            restored[key] = item
    return restored, unresolved


def restore_item(main_item):
    """Restore the equipment variant nested in a PackNotice item."""
    if not isinstance(main_item, dict):
        raise FieldConflictError('Capture contains a malformed inventory item.')
    result = dict(main_item)
    total = 0
    for source, schema in (('weapon','weapon'), ('armor','armor'), ('poster','poster'),
                           ('outfit','outfit')):
        nested = result.get(source)
        if not isinstance(nested, dict):
            continue
        cleaned, unresolved = restore(nested, schema)
        total += unresolved
        schemes = cleaned.get('dye_schemes') if source == 'outfit' else None
        if isinstance(schemes, dict) and schemes and all(isinstance(v, dict) for v in schemes.values()):
            schemes = list(schemes.values())
        if isinstance(schemes, list):
            restored_schemes = []
            for scheme in schemes:
                if isinstance(scheme, dict):
                    scheme, extra = restore(scheme, 'dyeScheme')
                    total += extra
                    colors = scheme.get('colors')
                    if isinstance(colors, dict) and colors and all(isinstance(v, dict) for v in colors.values()):
                        colors = list(colors.values())
                    if isinstance(colors, list):
                        restored_colors = []
                        for color in colors:
                            if isinstance(color, dict):
                                color, extra = restore(color, 'posColor')
                                total += extra
                            restored_colors.append(color)
                        scheme['colors'] = restored_colors
                restored_schemes.append(scheme)
            cleaned['dye_schemes'] = restored_schemes
        result[source] = cleaned
    return result, total


def restore_capture(main, entries):
    """Apply restoration to a whole capture and report what remains unresolved."""
    unresolved = 0
    for packet in entries.get('PackNotice', []):
        if not isinstance(packet, dict):
            raise FieldConflictError('Capture contains a malformed PackNotice response.')
        for detail in _objects(packet.get('items')):
            if not isinstance(detail, dict):
                continue
            item = detail.get('main_item')
            if isinstance(item, dict):
                detail['main_item'], count = restore_item(item)
                unresolved += count
            extras = detail.get('extras')
            if isinstance(extras, list):
                for index, extra in enumerate(extras):
                    if isinstance(extra, dict):
                        extras[index], count = restore_item(extra)
                        unresolved += count
    for packet in entries.get('PlayerMainDataRsp', []):
        if not isinstance(packet, dict):
            raise FieldConflictError('Capture contains malformed player data.')
        for character in _objects(packet.get('characters')):
            if not isinstance(character, dict):
                continue
            for preset in _objects(character.get('equipment_presets')):
                if not isinstance(preset, dict):
                    continue
                for key, schema in (('armors','armorInfo'), ('posters','posterInfo')):
                    values = preset.get(key)
                    if isinstance(values, list):
                        renamed = []
                        for value in values:
                            if isinstance(value, dict):
                                value, count = restore(value, schema)
                                unresolved += count
                            renamed.append(value)
                        preset[key] = renamed
            for preset in _objects(character.get('outfit_presets')):
                if isinstance(preset, dict):
                    unresolved += sum(1 for key in preset if isinstance(key, str) and FALLBACK.match(key))
    return {'restoredUnresolved': unresolved}
