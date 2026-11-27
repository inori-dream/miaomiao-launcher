"""UID gateway and local room administration for the Lolo server."""
from __future__ import annotations
import argparse
import asyncio
from contextlib import closing
import json
import re
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
import secrets
import sqlite3
import struct
import threading
import time
import uuid


def varint(value):
    out=bytearray()
    while value>127: out.append((value&127)|128);value>>=7
    out.append(value);return bytes(out)


def read_varint(data,position):
    value=0
    for shift in range(0,70,7):
        if position>=len(data):raise ValueError('Incomplete protobuf varint')
        byte=data[position];position+=1;value|=(byte&127)<<shift
        if byte<128:return value,position
    raise ValueError('Oversized protobuf varint')


def fields(data):
    result={};position=0
    while position<len(data):
        key,position=read_varint(data,position);number,wire=key>>3,key&7
        if not number:raise ValueError('Invalid protobuf field')
        if wire==0:value,position=read_varint(data,position)
        elif wire==2:
            size,position=read_varint(data,position);value=data[position:position+size];position+=size
        elif wire in (1,5):
            size=8 if wire==1 else 4;value=data[position:position+size];position+=size
        else:raise ValueError('Unsupported protobuf wire type')
        if position>len(data):raise ValueError('Truncated protobuf field')
        result.setdefault(number,[]).append(value)
    return result


def integer(number,value):return varint(number<<3)+varint(value)
def blob(number,value):return varint((number<<3)|2)+varint(len(value))+value


def snappy(data):
    size,position=read_varint(data,0)
    if size>16*1024*1024:raise ValueError('Compressed frame exceeds limit')
    out=bytearray()
    while position<len(data):
        tag=data[position];position+=1;kind=tag&3
        if kind==0:
            length=tag>>2
            if length<60:length+=1
            else:
                count=length-59
                if position+count>len(data):raise ValueError('Truncated Snappy literal length')
                length=int.from_bytes(data[position:position+count],'little')+1;position+=count
            if position+length>len(data):raise ValueError('Truncated Snappy literal')
            out.extend(data[position:position+length]);position+=length
        else:
            count={1:1,2:2,3:4}[kind]
            if position+count>len(data):raise ValueError('Truncated Snappy copy')
            if kind==1:length=4+((tag>>2)&7);offset=((tag&224)<<3)|data[position];position+=1
            elif kind==2:length=1+(tag>>2);offset=int.from_bytes(data[position:position+2],'little');position+=2
            else:length=1+(tag>>2);offset=int.from_bytes(data[position:position+4],'little');position+=4
            if not offset or offset>len(out):raise ValueError('Invalid Snappy copy')
            for _ in range(length):out.append(out[-offset])
        if len(out)>size:raise ValueError('Invalid Snappy size')
    if len(out)!=size:raise ValueError('Incomplete Snappy block')
    return bytes(out)


def packet(message,body,packet_id=0):
    head=integer(1,packet_id)+integer(2,message)+integer(4,len(body))+integer(6,1)
    return struct.pack('>H',len(head))+head+body


async def read_raw_frame(reader,state=None):
    """Read bounded framing only; body decoding must not block server relay."""
    if state is not None:
        state.update(phase='prefix',last_meta=state.get('meta',{}),meta={},header_len=0,body_len=0,name_backfilled=False)
    prefix=await reader.readexactly(2);size=struct.unpack('>H',prefix)[0]
    if state is not None:state.update(phase='header',header_len=size)
    if not 1<=size<=4096:raise ValueError('Invalid frame header')
    head=await reader.readexactly(size);meta=fields(head)
    # PacketHead scalars are uint32. Reject wrong wire types before readexactly.
    for number in range(1,7):
        if any(type(value) is not int or not 0<=value<=0xffffffff for value in meta.get(number,[])):
            raise ValueError('Invalid frame scalar')
        if number in meta:meta[number]=[meta[number][-1]]
    body_size=meta.get(4,[0])[0]
    if state is not None:state.update(phase='body',meta=meta,body_len=body_size)
    if not meta.get(2,[0])[0]:raise ValueError('Missing message ID')
    if body_size>4*1024*1024:raise ValueError('Oversized frame')
    body=await reader.readexactly(body_size)
    return prefix+head+body,meta,body


def decode_body(meta,body):
    return snappy(body) if meta.get(3,[0])[0]&1 else body


async def frame(reader):
    """Compatibility API: callers requiring decoded bodies remain fail closed."""
    raw,meta,body=await read_raw_frame(reader)
    return raw,meta,decode_body(meta,body)


def decode_observation(meta,body):
    """Optional server observation, never used to determine raw relay framing."""
    kind={1004:'PlayerLoginRsp',1006:'PlayerMainDataRsp',1016:'SceneDataNotice',1202:'ChangeSceneChannelRsp'}.get(meta[2][0])
    if kind is None:return None,None
    parsed=fields(decode_body(meta,body))
    if kind=='SceneDataNotice' and PROTO[kind]['Data'] in parsed:
        parsed=fields(parsed[PROTO[kind]['Data']][-1]);kind='SceneData'
    for source in ('Status','SceneId','ChannelId'):
        tag=PROTO[kind].get(source)
        if tag and tag in parsed and any(type(value) is not int for value in parsed[tag]):
            raise ValueError('Invalid observation scalar')
    return kind,parsed


def protobuf_spans(data):
    """Bounded wire scanner returning field and value spans for surgical rewrites."""
    position=0;result=[]
    while position<len(data):
        start=position;key,position=read_varint(data,position);number,wire=key>>3,key&7
        if not number:raise ValueError('Invalid protobuf field')
        value_start=position
        if wire==0:value,position=read_varint(data,position)
        elif wire==2:
            size,position=read_varint(data,position);value=data[position:position+size];position+=size
        elif wire in (1,5):
            size=8 if wire==1 else 4;value=data[position:position+size];position+=size
        else:raise ValueError('Unsupported protobuf wire type')
        if position>len(data):raise ValueError('Truncated protobuf field')
        result.append((number,wire,start,value_start,position,value))
    return result


def snappy_literal(data):
    """Encode a valid raw Snappy block without optional native dependencies."""
    if not data:return b'\x00'
    length=len(data)-1
    if length<60:tag=bytes([length<<2])
    else:
        count=(length.bit_length()+7)//8
        tag=bytes([(59+count)<<2])+length.to_bytes(count,'little')
    return varint(len(data))+tag+data


def backfill_login_name(raw,meta,body,name):
    """Best-effort local display-name compatibility; uncertain input stays exact."""
    if meta.get(2,[0])[0]!=1004 or not name:return raw,body,False
    try:
        if meta.get(5,[1])[0]>1 or meta.get(3,[0])[0]&~1:return raw,body,False
        decoded=decode_body(meta,body);parts=protobuf_spans(decoded)
        statuses=[value for number,wire,_,_,_,value in parts if number==1 and wire==0]
        if not statuses or any(value!=1 for value in statuses):return raw,body,False
        if any(number==1 and wire!=0 for number,wire,*_ in parts):return raw,body,False
        names=[(wire,value) for number,wire,_,_,_,value in parts if number==11]
        if any(wire!=2 or value for wire,value in names):return raw,body,False
        rewritten=b''.join(decoded[start:end] for number,_,start,_,end,_ in parts if number!=11)+blob(11,name.encode('utf-8'))
        encoded=snappy_literal(rewritten) if meta.get(3,[0])[0]&1 else rewritten
        if len(encoded)>4*1024*1024:return raw,body,False
        header_len=struct.unpack('>H',raw[:2])[0];header=raw[2:2+header_len]
        head_parts=protobuf_spans(header);chunks=[];found=False
        for number,wire,start,value_start,end,_ in head_parts:
            if number==4:
                if wire!=0:return raw,body,False
                chunks.append(header[start:value_start]+varint(len(encoded)));found=True
            else:chunks.append(header[start:end])
        if not found:chunks.append(integer(4,len(encoded)))
        rewritten_head=b''.join(chunks)
        if not 1<=len(rewritten_head)<=4096:return raw,body,False
        return struct.pack('>H',len(rewritten_head))+rewritten_head+encoded,encoded,True
    except Exception:
        # This compatibility path never converts a decode uncertainty into a close.
        return raw,body,False


class Manager:
    def __init__(self,profile,loop):
        self.profile=profile;self.loop=loop;self.sessions={};self.counter=0
        self.database=profile/'db/lolo.db';self.config_file=profile/'rooms.json'
        constants=json.loads((profile/'data/Constant.json').read_text(encoding='utf-8-sig'))
        default_id=int(constants.get('DefaultChannelId',constants.get('defaultChannelId',1314)))
        self.rooms=json.loads(self.config_file.read_text(encoding='utf-8')) if self.config_file.exists() else [{'id':default_id,'scene':0,'name':'默认房间','capacity':20,'closed':False}]
        self.key_file=profile/'admin-key.txt'
        self.key=self._admin_key()
        self.save()

    def _admin_key(self):
        if self.key_file.exists():
            try:
                value=self.key_file.read_text(encoding='ascii').strip()
                if re.fullmatch(r'[0-9a-f]{64}',value):return value
            except (OSError,UnicodeError):pass
            evidence=self.key_file.with_name(f'admin-key.corrupt-{uuid.uuid4().hex}.txt')
            self.key_file.replace(evidence)
        value=secrets.token_hex(32)
        temporary=self.key_file.with_name(f'.admin-key-{uuid.uuid4().hex}.tmp')
        try:
            temporary.write_text(value,encoding='ascii')
            temporary.replace(self.key_file)
        finally:temporary.unlink(missing_ok=True)
        return value

    def save(self):
        temporary=self.config_file.with_name(f'.rooms-{uuid.uuid4().hex}.tmp')
        try:
            temporary.write_text(json.dumps(self.rooms,ensure_ascii=False,indent=2),encoding='utf-8')
            temporary.replace(self.config_file)
        finally:temporary.unlink(missing_ok=True)

    def snapshot(self):
        online=[{'uid':s['uid'],'name':s.get('name',''),'ip':s['ip'],'room':s.get('room',0),'scene':s.get('scene',0),'ready':s.get('ready',False)} for s in self.sessions.values() if s.get('uid')]
        rooms=[]
        for room in self.rooms:
            if room.get('deleted'):continue
            value=dict(room);value['online']=sum(1 for p in online if p['room']==room['id']);value['state']='已关闭' if room['closed'] else '运行中' if value['online'] else '等待玩家';rooms.append(value)
        return {'rooms':rooms,'players':online,'online':len(online)}

    def account(self,sdk_uid):
        from capture_import import ensure_uid
        with closing(sqlite3.connect(self.database,timeout=10)) as connection:
            row=connection.execute('SELECT u.user_id,b.nick_name FROM of_user u LEFT JOIN of_game_basic b ON b.user_id=u.user_id WHERE sdk_uid=?',(sdk_uid,)).fetchone()
        if not row:
            ensure_uid(self.database,sdk_uid);return sdk_uid,f'UID {sdk_uid}'
        return row[0],row[1] or f'UID {row[0]}'

    async def action(self,body):
        action=body.get('action');rid=int(body.get('room',0));uid=int(body.get('uid',0))
        room=next((r for r in self.rooms if r['id']==rid),None)
        if action=='create':
            rid=max([r['id'] for r in self.rooms if r['id']<100000]+[0])+1
            if rid>=100000:raise ValueError('Public room IDs are exhausted')
            name=str(body.get('name','')).strip();capacity=int(body.get('capacity',20));scene=int(body.get('scene',0))
            if not 1<=len(name)<=32 or not 1<=capacity<=50 or scene<0:raise ValueError('Invalid room settings')
            self.rooms.append({'id':rid,'scene':scene,'name':name,'capacity':capacity,'closed':False});self.save()
        elif action in ('close','open','capacity','delete'):
            if not room:raise ValueError('Room not found')
            if action=='capacity':
                capacity=int(body.get('capacity',20))
                if not 1<=capacity<=50:raise ValueError('Capacity must be between 1 and 50')
                room['capacity']=capacity
            else:room['closed']=action in ('close','delete')
            if action=='delete':room['deleted']=True
            self.save()
            if action in ('close','delete'):
                for session in list(self.sessions.values()):
                    if session.get('room')==rid:session['client'].close();session['server'].close()
        elif action=='kick':
            sessions=[s for s in self.sessions.values() if s.get('uid')==uid]
            if not sessions:raise ValueError('Player is not online')
            for session in sessions:session['client'].close();session['server'].close()
        elif action=='move':
            if not room or room['closed']:raise ValueError('Target room is unavailable')
            session=next((s for s in self.sessions.values() if s.get('uid')==uid and s.get('ready')),None)
            if not session:raise ValueError('Player is not ready')
            if sum(s.get('room')==rid for s in self.sessions.values())>=room['capacity']:raise ValueError('Target room is full')
            await self.move(session,room)
        elif action is not None:raise ValueError('Unknown room operation')
        return self.snapshot()

    async def move(self,session,room):
        tags=PROTO['ChangeSceneChannelReq'];scene=room.get('scene') or session.get('scene',0)
        body=integer(tags['SceneId'],scene)+integer(tags['ChannelLabel'],room['id'])
        async with session['lock']:
            session['server'].write(packet(1201,body));await session['server'].drain()
        session['wanted']=room['id']

    async def connect(self,reader,writer,backend_port):
        server=None;key=None;tasks=[];reported=set();conn=secrets.token_hex(6)
        states={direction:{'phase':'start','meta':{},'read':0,'forwarded':0} for direction in ('client','server')}
        seen=set()
        def log(event,direction,**extra):
            state=states[direction];meta=state['meta']
            value={'event':event,'conn':conn,'direction':direction,'phase':state['phase'],
                   'msgid':meta.get(2,[0])[0],'flags':meta.get(3,[0])[0],
                   'count':meta.get(5,[0])[0],'seq':meta.get(6,[0])[0],'packet_id':meta.get(1,[0])[0],
                   'header_len':state.get('header_len',0),'body_len':state.get('body_len',0),
                   'read':state['read'],'forwarded':state['forwarded']}
            if value['msgid']==1004:value['name_backfilled']=state.get('name_backfilled',False)
            if state.get('last_meta'):value['last_msgid']=state['last_meta'].get(2,[0])[0]
            value.update(extra);print(json.dumps(value,ensure_ascii=False),flush=True)
        def handshake(event,direction):
            message=states[direction]['meta'].get(2,[0])[0];marker=(event,direction,message)
            if 1001<=message<=1006 and marker not in seen:
                seen.add(marker);log(event,direction)
        async def read(source,direction):
            raw,head,body=await read_raw_frame(source,states[direction])
            states[direction]['read']+=1;states[direction]['phase']='read'
            handshake('frame-read',direction)
            return raw,head,body
        async def forward(target,raw,direction):
            states[direction]['phase']='write';target.write(raw)
            states[direction]['phase']='drain';await target.drain()
            states[direction]['forwarded']+=1;states[direction]['phase']='forwarded'
            handshake('frame-forwarded',direction)
        def ended(error,direction):
            if isinstance(error,asyncio.IncompleteReadError):
                log('connection-eof',direction,reason=type(error).__name__,partial_len=len(error.partial),expected=error.expected)
            else:log('connection-error',direction,reason=type(error).__name__)
        def inspect(task):
            if task in reported:return
            reported.add(task)
            if not task.cancelled():
                error=task.exception()
                if error is not None:ended(error,task.get_name().rsplit(':',1)[-1])
        try:
            raw,head,body=await asyncio.wait_for(read(reader,'client'),20)
            states['client']['phase']='authenticate'
            if head[2][0]!=1001:raise ValueError('UID login must be the first request')
            uid_value=fields(decode_body(head,body)).get(PROTO['VerifyLoginTokenReq']['SdkUid'],[b''])[-1].decode('ascii')
            sdk_uid=int(uid_value)
            if not 1<=sdk_uid<=4294967295:raise ValueError('Invalid UID')
            uid,name=await asyncio.to_thread(self.account,sdk_uid)
            available=[room for room in self.rooms if not room['closed'] and sum(s.get('wanted')==room['id'] or s.get('room')==room['id'] for s in self.sessions.values())<room['capacity']]
            if not available:raise ValueError('No open room has capacity')
            states['client']['phase']='backend-connect'
            upstream,server=await asyncio.open_connection('127.0.0.1',backend_port)
            self.counter+=1;key=self.counter
            peer=writer.get_extra_info('peername')
            session={'uid':uid,'name':name,'ip':peer[0] if peer else '', 'client':writer,'server':server,'lock':asyncio.Lock(),'wanted':available[0]['id'],'room':0,'scene':0,'ready':None}
            self.sessions[key]=session
            await forward(server,raw,'client')
            log('uid-connected','client')
            async def from_client():
                while True:
                    raw,head,body=await read(reader,'client')
                    if head[2][0]==1201:
                        states['client']['phase']='authorize'
                        parsed=fields(decode_body(head,body));labels=parsed.get(PROTO['ChangeSceneChannelReq']['ChannelLabel'],[session['room']])
                        if any(type(value) is not int or not 0<=value<=0xffffffff for value in labels):raise ValueError('Invalid room ID')
                        rid=labels[-1]
                        room=next((r for r in self.rooms if r['id']==rid),None)
                        # Unknown channels belong to the backend; register them only
                        # after a server observation, not by trusting this request.
                        if room and (room['closed'] or (sum(s.get('room')==rid for s in self.sessions.values())>=room['capacity'] and session['room']!=rid)):raise ValueError('Room is unavailable')
                    async with session['lock']:await forward(server,raw,'client')
            async def from_server():
                while True:
                    raw,head,body=await read(upstream,'server');message=head[2][0]
                    # The sole pre-relay interpretation is the narrowly scoped empty-name
                    # compatibility fix; uncertain input returns the original exact frame.
                    if message==1004:
                        states['server']['phase']='name-backfill'
                        raw,body,changed=backfill_login_name(raw,head,body,session['name'])
                        states['server']['name_backfilled']=changed
                        if changed:
                            head=dict(head);head[4]=[len(body)]
                            states['server'].update(meta=head,body_len=len(body),header_len=struct.unpack('>H',raw[:2])[0])
                    # All optional room observation still runs strictly after write/drain.
                    await forward(writer,raw,'server')
                    if message not in (1004,1006,1016,1202):continue
                    states['server']['phase']='observe'
                    try:kind,parsed=decode_observation(head,body)
                    except Exception as error:
                        session['ready']=None
                        log('observe-error','server',reason=type(error).__name__)
                        continue
                    for source,target in [('SceneId','scene'),('ChannelId','room')]:
                        tag=PROTO[kind].get(source)
                        if tag and tag in parsed:session[target]=parsed[tag][-1]
                    if message==1006:
                        values=parsed.get(PROTO[kind]['Status'],[])
                        status=values[-1] if values else None
                        session['ready']=status==1 if status is not None else None
                        marker=('main-data','server',message)
                        if marker not in seen:
                            seen.add(marker);log('main-data','server',status=status,characters=len(parsed.get(PROTO[kind]['Characters'],[])))
                        if session['ready'] is True and session['room']!=session['wanted']:
                            room=next((r for r in self.rooms if r['id']==session['wanted']),None)
                            if room:
                                states['server']['phase']='move';await self.move(session,room)
                    if session['room']:
                        room=next((r for r in self.rooms if r['id']==session['room']),None)
                        if not room:
                            states['server']['phase']='save-room'
                            self.rooms.append({'id':session['room'],'scene':session['scene'],'name':f'场景房间 {session["room"]}','capacity':50,'closed':False});self.save()
            tasks=[asyncio.create_task(from_client(),name=f'{conn}:client'),asyncio.create_task(from_server(),name=f'{conn}:server')]
            done,pending=await asyncio.wait(tasks,return_when=asyncio.FIRST_COMPLETED)
            for task in done:inspect(task)
            for task in pending:task.cancel()
            await asyncio.gather(*tasks,return_exceptions=True)
            for task in tasks:inspect(task)
        except asyncio.CancelledError:
            ended(asyncio.CancelledError(),'client')
            raise
        except Exception as error:ended(error,'client')
        finally:
            for task in tasks:
                if not task.done():task.cancel()
            if tasks:
                await asyncio.gather(*tasks,return_exceptions=True)
                for task in tasks:inspect(task)
            if key:self.sessions.pop(key,None)
            writer.close()
            if server:server.close()
            for direction in states:log('connection-closed',direction)


class Admin(BaseHTTPRequestHandler):
    manager=None
    def log_message(self,*args):pass
    def reply(self,status,value):
        data=json.dumps(value,ensure_ascii=False).encode('utf-8');self.send_response(status);self.send_header('Content-Type','application/json; charset=utf-8');self.send_header('Content-Length',str(len(data)));self.end_headers();self.wfile.write(data)
    def authorized(self):return secrets.compare_digest(self.headers.get('X-World-Key',''),self.manager.key)
    def do_GET(self):
        if not self.authorized():self.reply(403,{'error':'Local administration key required'});return
        future=asyncio.run_coroutine_threadsafe(self.manager.action({}),self.manager.loop)
        try:self.reply(200,future.result(5))
        except Exception as error:self.reply(400,{'error':str(error)})
    def do_POST(self):
        if not self.authorized():self.reply(403,{'error':'Local administration key required'});return
        try:
            length=int(self.headers.get('Content-Length','0'))
            if length>8192:raise ValueError('Request too large')
            future=asyncio.run_coroutine_threadsafe(self.manager.action(json.loads(self.rfile.read(length))),self.manager.loop);self.reply(200,future.result(5))
        except Exception as error:self.reply(400,{'error':str(error)})


async def main(args):
    manager=Manager(args.profile,asyncio.get_running_loop());Admin.manager=manager
    http=None;server=None;thread=None;thread_started=False
    try:
        http=ThreadingHTTPServer(('127.0.0.1',args.admin_port),Admin)
        thread=threading.Thread(target=http.serve_forever,daemon=True);thread.start();thread_started=True
        server=await asyncio.start_server(lambda r,w:manager.connect(r,w,args.backend_port),args.host,args.port)
        print(json.dumps({'event':'gateway-ready','port':args.port,'adminPort':args.admin_port}),flush=True)
        async with server:await server.serve_forever()
    finally:
        # A gateway bind failure must not leave the admin listener alive in an
        # embedded/test process or when asyncio propagates the failure.
        if server is not None:
            server.close();await server.wait_closed()
        if http is not None:
            if thread_started:http.shutdown()
            http.server_close()
        if thread_started:thread.join(timeout=5)


PROTO=json.loads(Path(__file__).with_name('proto_fields.json').read_text(encoding='utf-8'))
if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('--profile',type=Path,required=True);parser.add_argument('--host',required=True);parser.add_argument('--port',type=int,required=True);parser.add_argument('--backend-port',type=int,required=True);parser.add_argument('--admin-port',type=int,required=True)
    asyncio.run(main(parser.parse_args()))
