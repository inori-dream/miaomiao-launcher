// Multi-language localization table supporting 5 runtime locales:
// zh-CN (Simplified Chinese), zh-TW (Traditional Chinese), en (English),
// ja (Japanese), and ko (Korean). Keeps static defaults compatible with
// CN (zh-CN) and EN (#if ENGLISH -> en) builds while allowing live switching.
using System;
using System.Collections.Generic;
using System.Text.RegularExpressions;

namespace OverFieldLocal
{
    static class Loc
    {
        public static readonly bool DefaultEnglish =
#if ENGLISH
            true;
#else
            false;
#endif
        // Not readonly/const: tests inspect this field via reflection at startup,
        // and runtime language switching updates it when switching to/from English.
        public static bool English = DefaultEnglish;

        public static string CurrentLocale = DefaultEnglish ? "en" : "zh-CN";

        public static readonly string[] SupportedLocales = { "zh-CN", "zh-TW", "en", "ja", "ko" };
        public static readonly string[] LocaleDisplayNames = { "简体中文", "繁體中文", "English", "日本語", "한국어" };

        public static string NormalizeLocale(string value)
        {
            if (String.IsNullOrWhiteSpace(value)) return DefaultEnglish ? "en" : "zh-CN";
            string v = value.Trim().Replace('_', '-').ToLowerInvariant();
            if (v == "zh-tw" || v == "zh-hk" || v == "zh-hant" || v == "tw" || v == "tc") return "zh-TW";
            if (v.StartsWith("zh") || v == "cn" || v == "sc") return "zh-CN";
            if (v.StartsWith("en")) return "en";
            if (v.StartsWith("ja") || v == "jp") return "ja";
            if (v.StartsWith("ko") || v == "kr") return "ko";
            return DefaultEnglish ? "en" : "zh-CN";
        }

        public static int LocaleIndex(string locale)
        {
            string normalized = NormalizeLocale(locale);
            int idx = Array.IndexOf(SupportedLocales, normalized);
            return idx >= 0 ? idx : 0;
        }

        public static void SetLocale(string locale)
        {
            CurrentLocale = NormalizeLocale(locale);
            English = CurrentLocale == "en";
        }

        static readonly Regex GeoJsonCountryRegex = new Regex("\"country(?:_?code|Code)?\"\\s*:\\s*\"([A-Za-z]{2})\"", RegexOptions.CultureInvariant);

        static bool IsAsciiLetter(char c) { return (c >= 'A' && c <= 'Z') || (c >= 'a' && c <= 'z'); }

        public static string ParseGeoCountryCode(string body)
        {
            if (String.IsNullOrWhiteSpace(body)) return "";
            string trimmed = body.Trim();
            if (trimmed.Length == 2 && IsAsciiLetter(trimmed[0]) && IsAsciiLetter(trimmed[1]))
                return trimmed.ToUpperInvariant();
            var match = GeoJsonCountryRegex.Match(trimmed);
            if (match.Success) return match.Groups[1].Value.ToUpperInvariant();
            return "";
        }

        public static string LocaleFromCountryCode(string countryCode)
        {
            string fallback = DefaultEnglish ? "en" : "zh-CN";
            string code = ParseGeoCountryCode(countryCode);
            if (String.IsNullOrEmpty(code) || code == "XX" || code == "T1") return fallback;
            if (code == "CN") return "zh-CN";
            if (code == "TW" || code == "HK" || code == "MO") return "zh-TW";
            if (code == "JP") return "ja";
            if (code == "KR") return "ko";
            return "en";
        }

        struct LocaleEntry
        {
            public readonly string Tw, En, Ja, Ko;
            public LocaleEntry(string tw, string en, string ja, string ko)
            { Tw = tw; En = en; Ja = ja; Ko = ko; }
        }

        static readonly Dictionary<string, LocaleEntry> Entries = BuildEntries();

        static void Add(Dictionary<string, LocaleEntry> d, string zh, string tw, string en, string ja, string ko)
        {
            d[zh] = new LocaleEntry(tw, en, ja, ko);
        }

        static Dictionary<string, LocaleEntry> BuildEntries()
        {
            var d = new Dictionary<string, LocaleEntry>(StringComparer.Ordinal);

            // Core UI & Preflight
            Add(d, "语言", "語言", "Language", "言語", "언어");
            Add(d, "存档 A · 全区服可用", "存檔 A · 全區服可用", "Save A · All channels", "セーブA · 全サーバー可", "저장 A · 전 서버");
            Add(d, "存档 B · 全区服可用", "存檔 B · 全區服可用", "Save B · All channels", "セーブB · 全サーバー可", "저장 B · 전 서버");
            Add(d, "局域网联机 · 全区服可用", "區網連線 · 全區服可用", "LAN co-op · All channels", "LANマルチ · 全サーバー", "LAN 멀티 · 전 서버");
            Add(d, "官方采集（B服 / Tap / Steam）", "官方採集（B服 / Tap / Steam）", "Capture (Bili/Tap/Steam)", "収集（全サーバー共通）", "공식 수집 (전 서버)");
            Add(d, "单机存档 A · 支持 B服 / TapTap / Steam · 独立数据目录", "單機存檔 A · 支援 B服 / TapTap / Steam · 獨立資料目錄", "Solo Save A · Bilibili / TapTap / Steam supported", "セーブ A · Bilibili / TapTap / Steam 対応", "솔로 저장 A · Bilibili / TapTap / Steam 지원");
            Add(d, "独立存档 B · 支持 B服 / TapTap / Steam · 独立数据目录", "獨立存檔 B · 支援 B服 / TapTap / Steam · 獨立資料目錄", "Solo Save B · Bilibili / TapTap / Steam supported", "セーブ B · Bilibili / TapTap / Steam 対応", "독립 저장 B · Bilibili / TapTap / Steam 지원");
            Add(d, "局域网服务端 · 按 UID 管理 · 支持 B服 / TapTap / Steam", "區網服務端 · 按 UID 管理 · 支援 B服 / TapTap / Steam", "LAN Server · Per-UID · Bilibili / TapTap / Steam supported", "LAN サーバー · Bilibili / TapTap / Steam 対応", "LAN 서버 · Bilibili / TapTap / Steam 지원");
            Add(d, "游戏区服", "遊戲區服", "Server", "サーバー", "게임 서버");
            Add(d, "采集区服", "採集區服", "Server", "収集対象", "수집 서버");
            Add(d, "自动识别（全区服通用）", "自動識別（全區服通用）", "Auto-detect (Bilibili / TapTap / Steam)", "自動判別（全サーバー対応）", "자동 감지 (Bilibili / TapTap / Steam)");
            Add(d, "B服（Bilibili）", "B服（Bilibili）", "Bilibili Server", "Bilibili サーバー", "Bilibili 서버");
            Add(d, "TapTap 服", "TapTap 服", "TapTap Server", "TapTap サーバー", "TapTap 서버");
            Add(d, "Steam 服", "Steam 服", "Steam Server", "Steam サーバー", "Steam 서버");
            Add(d, "查找 Steam", "尋找 Steam", "Find Steam", "Steam検出", "Steam 찾기");
            Add(d, "自动查找", "自動尋找", "Auto-detect", "自動検出", "자동 찾기");
            Add(d, "已识别：Bilibili（PCGameSDK）", "已識別：Bilibili（PCGameSDK）", "Detected: Bilibili (PCGameSDK)", "検出：Bilibili (PCGameSDK)", "감지됨: Bilibili (PCGameSDK)");
            Add(d, "已识别：TapTap（QuickSDK PC）", "已識別：TapTap（QuickSDK PC）", "Detected: TapTap (QuickSDK PC)", "検出：TapTap (QuickSDK PC)", "감지됨: TapTap (QuickSDK PC)");
            Add(d, "已识别：Steam（QuickSDK Overseas）", "已識別：Steam（QuickSDK Overseas）", "Detected: Steam (QuickSDK)", "検出：Steam (QuickSDK)", "감지됨: Steam (QuickSDK)");
            Add(d, "尚未配置客户端目录", "尚未配置用戶端目錄", "Client folder not set", "クライアント未設定", "클라이언트 미설정");
            Add(d, "未在当前目录识别到有效客户端", "未在當前目錄識別到有效用戶端", "No valid client detected here", "有効なクライアント未検出", "유효한 클라이언트 미감지");
            Add(d, "已找到 Steam 客户端：", "已找到 Steam 用戶端：", "Found Steam client: ", "Steam クライアントを検出しました：", "Steam 클라이언트를 찾았습니다: ");
            Add(d, "未在本机 Steam 库中检测到《开放空间》(App 3879660)，请点击右侧按钮手动选择客户端目录或快捷方式。", "未在本機 Steam 庫中偵測到《開放空間》(App 3879660)，請點擊右側按鈕手動選擇用戶端目錄或捷徑。", "OverField (App 3879660) was not found in local Steam libraries. Browse manually for the game folder or shortcut.", "ローカル Steam ライブラリで OverField (App 3879660) が見つかりませんでした。右側のボタンから手動で選択してください。", "로컬 Steam 라이브러리에서 OverField (App 3879660)를 찾을 수 없습니다. 오른쪽 버튼으로 직접 선택해 주세요.");

            // Preflight & Admin
            Add(d, "预检查", "預檢查", "Preflight", "事前確認", "사전 점검");
            Add(d, "正在预检查采集环境", "正在預檢查採集環境", "Checking capture environment", "収集環境を確認中", "수집 환경 점검 중");
            Add(d, "采集预检查", "採集預檢查", "Capture preflight", "収集事前チェック", "수집 사전 점검");
            Add(d, "采集依赖检查通过。", "採集依賴檢查通過。", "Capture dependency checks passed.", "収集依存チェックに合格しました。", "수집 의존성 점검을 통과했습니다.");
            Add(d, "需要管理员确认；提升权限后会重新检查。", "需要管理員確認；提升權限後會重新檢查。", "Administrator approval needed; checks run again after elevation.", "管理者権限が必要です。昇格後に再チェックされます。", "관리자 승인이 필요합니다. 권한 상승 후 다시 확인합니다.");
            Add(d, "预检查未通过，请按检查结果处理后重试。", "預檢查未通過，請按檢查結果處理後重試。", "Preflight did not pass. Resolve the reported issues and retry.", "事前チェックに失敗しました。表示された項目を解決して再試行してください。", "사전 점검을 통과하지 못했습니다. 표시된 문제를 해결한 후 다시 시도하세요.");
            Add(d, "通过", "通過", "OK", "OK", "통과");
            Add(d, "注意", "注意", "Warning", "注意", "주의");
            Add(d, "失败", "失敗", "Error", "エラー", "오류");
            Add(d, "未确认", "未確認", "Unknown", "不明", "미확인");
            Add(d, "请先退出旧启动器，再以管理员身份重新启动。", "請先退出舊啟動器，再以管理員身份重新啟動。", "Close the previous launcher, then restart as administrator.", "前のランチャーを終了してから管理者として再起動してください。", "이전 런처를 종료한 뒤 관리자 권한으로 다시 실행하세요.");
            Add(d, "无法确认旧启动器已退出，请手动以管理员身份重启。", "無法確認舊啟動器已退出，請手動以管理員身份重啟。", "Cannot confirm the previous launcher exited. Restart manually as administrator.", "前のランチャーの終了を確認できませんでした。手動で管理者として再起動してください。", "이전 런처 종료를 확인할 수 없습니다. 수동으로 관리자 권한으로 재실행하세요.");
            Add(d, "正在申请管理员权限；取消不会启动游戏或采集。", "正在申請管理員權限；取消不會啟動遊戲或採集。", "Requesting administrator approval. Cancelling starts neither capture nor game.", "管理者権限を要求中です。キャンセルしてもゲームや収集は開始されません。", "관리자 권한을 요청하고 있습니다. 취소해도 게임이나 수집이 시작되지 않습니다.");
            Add(d, "未启动管理员实例，请重试。", "未啟動管理員執行個體，請重試。", "Administrator instance did not start. Please retry.", "管理者権限での起動に失敗しました。もう一度お試しください。", "관리자 인스턴스를 시작하지 못했습니다. 다시 시도하세요.");

            // Decode & Capture summary
            Add(d, "成功解码响应/消息数：", "成功解碼回應/訊息數：", "Successfully decoded messages: ", "正常に復号されたメッセージ数：", "정상 디코딩된 메시지 수: ");
            Add(d, "发现残缺流尾；请查看采集报告，不能视为完整备份。", "發現殘缺串流尾；請查看採集報告，不能視為完整備份。", "Incomplete stream tails detected. Check the report; this is not a complete backup.", "不完全なストリーム末尾が検出されました。レポートを確認してください（完全なバックアップではありません）。", "불완전한 스트림 끝부분이 감지되었습니다. 보고서를 확인하세요(완전한 백업이 아님).");
            Add(d, "检测到抓包缺口或未解码数据，备份可能不完整。", "偵測到封包缺口或未解碼資料，備份可能不完整。", "A capture gap or undecoded data was found. The backup may be incomplete.", "パケット欠落または未復号データが検出されました。バックアップが不完全な可能性があります。", "패킷 누락 또는 미디코딩 데이터가 감지되었습니다. 백업이 불완전할 수 있습니다.");
            Add(d, "探索观测报告已加入账号ZIP；总量未验证，不表示全图完整。", "探索觀測報告已加入帳號ZIP；總量未驗證，不表示全圖完整。", "Exploration evidence is included in the ZIP. Totals are unverified; it is not a complete map snapshot.", "探索観測レポートをアカウントZIPに追加しました。総数は未検証であり全マップ網羅は保証されません。", "탐험 관측 보고서가 계정 ZIP에 포함되었습니다. 총량은 검증되지 않았으며 전체 맵 완료를 의미하지 않습니다.");
            Add(d, "探索报告已生成，但尚未收到有效探索证据；未知不当0。", "探索報告已生成，但尚未收到有效探索證據；未知不當0。", "Exploration report generated, but no valid evidence was received. Unknown is not zero.", "探索レポートを生成しましたが有効な探索データがありません。不明箇所は0扱いされません。", "탐험 보고서가 생성되었으나 유효한 탐험 증거를 받지 못했습니다. 미확인 항목은 0으로 처리되지 않습니다.");
            Add(d, "采集项目清单 · 1.0.12增强集成 · 实时/最终核对", "採集項目清單 · 1.0.12增強整合 · 即時/最終核對", "Capture checklist · 1.0.12 integration · live/final checks", "収集チェックリスト · ライブ / 完了時確認", "수집 체크리스트 · 실시간 / 최종 검증");

            // Suffix / Fragment patterns
            Add(d, " 已有存档，未写入。请先备份该账号或换用其他 UID。", " 已有存檔，未寫入。請先備份該帳號或換用其他 UID。", " already has a save and was not written. Back that account up first or use another UID.", " には既にセーブデータがあるため書き込みませんでした。バックアップするか別の UID を使ってください。", " 에 이미 저장 데이터가 있어 기록하지 않았습니다. 백업하거나 다른 UID를 사용하세요.");
            Add(d, "已有存档，未写入。请先备份该账号或换用其他 UID。", "已有存檔，未寫入。請先備份該帳號或換用其他 UID。", " already has a save and was not written. Back that account up first or use another UID.", " には既にセーブデータがあるため書き込みませんでした。", " 에 이미 저장 데이터가 있어 기록하지 않았습니다.");
            Add(d, " 个角色\n\n确认写入？", " 個角色\n\n確認寫入？", " characters\n\nWrite it now?", " 名のキャラクター\n\n書き込みますか？", "명의 캐릭터\n\n지금 기록하시겠습니까?");
            Add(d, "个角色\n\n确认写入？", "個角色\n\n確認寫入？", " characters\n\nWrite it now?", "名のキャラクター\n\n書き込みますか？", "명의 캐릭터\n\n지금 기록하시겠습니까?");
            Add(d, " 个，装备响应 ", " 個，裝備回應 ", " placeholders, equipment responses ", " 件、装備応答 ", "개, 장비 응답 ");
            Add(d, "个，装备响应 ", "個，裝備回應 ", " placeholders, equipment responses ", "件、装備応答 ", "개, 장비 응답 ");
            Add(d, " 条", " 條", " entries", " 件", "건");
            Add(d, "条", "條", " entries", "件", "건");
            Add(d, " 个数据包", " 個封包", " packets", " パケット", "개 패킷");
            Add(d, "个数据包", "個封包", " packets", " パケット", "개 패킷");
            Add(d, " 个角色\n\n将导入当前模式的独立数据目录。已有数据会先备份；其他 UID 保留。\n运行中的服务会先停止，并断开在线玩家。\n角色、装备和背包会写入存档，尚未支持的模块会保留原始抓包数据。", " 個角色\n\n將匯入當前模式的獨立資料目錄。已有資料會先備份；其他 UID 保留。\n運行中的服務會先停止，並斷開線上玩家。\n角色、裝備與背包會寫入存檔，尚未支援的模組會保留原始抓包資料。", " characters\n\nIt is imported into this mode's isolated data folder. Existing data is backed up first and other UIDs are kept.\nRunning services stop first and online players are disconnected.\nCharacters, equipment and the bag are written to the save; modules that are not supported yet keep their raw captured data.", " 名のキャラクター\n\n現在のモードの専用フォルダに取り込みます。既存データは事前にバックアップされ、他の UID は保持されます。\n稼働中のサービスは一度停止します。\nキャラクター・装備・バッグは保存され、未対応モジュールの生データも保持されます。", "명의 캐릭터\n\n현재 모드의 독립 데이터 폴더로 가져옵니다. 기존 데이터는 먼저 백업되며 다른 UID는 유지됩니다.\n실행 중인 서비스는 먼저 중지됩니다.\n캐릭터, 장비, 가방이 저장되며 미지원 모듈은 원본 패킷 데이터가 보관됩니다.");
            Add(d, "个角色\n\n将导入当前模式的独立数据目录。已有数据会先备份；其他 UID 保留。\n运行中的服务会先停止，并断开在线玩家。\n角色、装备和背包会写入存档，尚未支持的模块会保留原始抓包数据。", "個角色\n\n將匯入當前模式的獨立資料目錄。已有資料會先備份；其他 UID 保留。\n運行中的服務會先停止，並斷開線上玩家。\n角色、裝備與背包會寫入存檔，尚未支援的模組會保留原始抓包資料。", " characters\n\nIt is imported into this mode's isolated data folder. Existing data is backed up first and other UIDs are kept.\nRunning services stop first and online players are disconnected.\nCharacters, equipment and the bag are written to the save; modules that are not supported yet keep their raw captured data.", " 名のキャラクター\n\n現在のモードの専用フォルダに取り込みます。", "명의 캐릭터\n\n현재 모드의 독립 데이터 폴더로 가져옵니다.");
            Add(d, " 的资料。原数据已有备份，其他 UID 保留。", " 的資料。原資料已有備份，其他 UID 保留。", "'s data. The previous data is backed up and other UIDs are kept.", " のデータを取り込みました。元データはバックアップ済みです。", "의 데이터를 가져왔습니다. 기존 데이터는 백업되었습니다.");
            Add(d, "的资料。原数据已有备份，其他 UID 保留。", "的資料。原資料已有備份，其他 UID 保留。", "'s data. The previous data is backed up and other UIDs are kept.", " のデータを取り込みました。", "의 데이터를 가져왔습니다.");
            Add(d, " 的本地游戏，请稍候…", " 的本地遊戲，請稍候…", "'s local game, please wait…", " のローカルゲームを起動しています…", " 로컬 게임을 준비 중입니다…");
            Add(d, "的本地游戏，请稍候…", "的本地遊戲，請稍候…", "'s local game, please wait…", " のローカルゲームを起動しています…", " 로컬 게임을 준비 중입니다…");
            Add(d, " 服务，请稍候…", " 服務，請稍候…", " services, please wait…", " サービスを起動しています…", " 서비스를 시작 중입니다…");
            Add(d, "服务，请稍候…", "服務，請稍候…", " services, please wait…", " サービスを起動しています…", " 서비스를 시작 중입니다…");
            Add(d, " 登录。", " 登入。", " now.", " でログインします。", " 으로 로그인합니다.");
            Add(d, "登录。", "登入。", " now.", " でログインします。", " 으로 로그인합니다.");
            Add(d, " 更新", " 更新", "Update", " 更新", " 업데이트");
            Add(d, "更新", "更新", "Update", "更新", "업데이트");

            // Modes & Checklist states & items
            Add(d, "国际服", "備用存檔 B / 國際服", "International", "独立セーブ B / 国際", "독립 저장 B / 글로벌");
            Add(d, "已采集", "已採集", "Collected", "収集済み", "수집 완료");
            Add(d, "未见装备列表", "未見裝備清單", "Equipment list missing", "装備一覧なし", "장비 목록 누락");
            Add(d, "已收到（列表为空）", "已收到（清單為空）", "Received (empty list)", "受信済み（空）", "수신됨(빈 목록)");
            Add(d, "尚未采集", "尚未採集", "Waiting", "待機中", "수집 대기");
            Add(d, "未采集到", "未採集到", "Missing", "未取得", "누락됨");
            Add(d, "暂时无法判断", "暫時無法判斷", "Unknown", "判定不可", "확인 불가");

            // Checklist Items & Hints (including Fishing & Gathering)
            Add(d, "登录基础信息", "登入基礎資訊", "Login basics", "ログイン基本情報", "로그인 기본 정보");
            Add(d, "重新登录公服", "重新登入公服", "Sign in again", "ゲームに再ログイン", "공식 서버 재로그인");
            Add(d, "UID、等级与任务主数据", "UID、等級與任務主資料", "UID, level and main quest data", "UID・レベル・任務メインデータ", "UID, 레벨 및 메인 퀘스트");
            Add(d, "登录并进入场景", "登入並進入場景", "Sign in and enter a scene", "ログインしてマップに入る", "로그인 후 맵 입장");
            Add(d, "角色与外观", "角色與外觀", "Characters and appearance", "キャラクター・外見", "캐릭터 및 외형");
            Add(d, "打开角色、穿搭界面", "打開角色、穿搭介面", "Open characters and outfits", "キャラ・コーデ画面を開く", "캐릭터 및 코디 화면 열기");
            Add(d, "背包物品", "背包物品", "Bag items", "バッグのアイテム", "가방 아이템");
            Add(d, "打开背包并翻页", "打開背包並翻頁", "Open the bag and browse pages", "バッグを開いてページを送る", "가방 열고 페이지 넘기기");
            Add(d, "武器", "武器", "Weapons", "武器", "무기");
            Add(d, "打开武器、装备界面", "打開武器、裝備介面", "Open weapons and equipment", "武器・装備画面を開く", "무기·장비 화면 열기");
            Add(d, "护甲", "護甲", "Armor", "防具", "방어구");
            Add(d, "打开护甲、装备界面", "打開護甲、裝備介面", "Open armor and equipment", "防具・装備画面を開く", "방어구·장비 화면 열기");
            Add(d, "装备海报", "裝備海報", "Equipment posters", "装備ポスター", "장비 포스터");
            Add(d, "打开海报装备界面", "打開海報裝備介面", "Open equipment posters", "ポスター装備画面を開く", "포스터 장비 화면 열기");
            Add(d, "宠物列表", "寵物清單", "Pets", "ペット一覧", "펫 목록");
            Add(d, "打开宠物、仓库界面", "打開寵物、倉庫介面", "Open pets and storage", "ペット・倉庫画面を開く", "펫 및 창고 화면 열기");
            Add(d, "家园与家具", "家園與家具", "Garden and furniture", "マイガーデン・家具", "마이홈 및 가구");
            Add(d, "进入自己的家园，等待加载", "進入自己的家園，等待載入", "Enter your garden and let it load", "自分のマイガーデンに入って読み込みを待つ", "자신의 마이홈에 들어가 로딩 대기");
            Add(d, "邮件列表", "郵件清單", "Mail", "メール一覧", "우편함");
            Add(d, "打开邮件界面", "打開郵件介面", "Open mail", "メール画面を開く", "우편 화면 열기");
            Add(d, "卡池信息", "卡池資訊", "Banner info", "ガチャ情報", "가챠 배너");
            Add(d, "打开抽卡界面", "打開抽卡介面", "Open gacha", "ガチャ画面を開く", "가챠 화면 열기");
            Add(d, "抽卡记录", "抽卡紀錄", "Gacha history", "ガチャ履歴", "가챠 기록");
            Add(d, "打开抽卡历史并翻页", "打開抽卡歷史並翻頁", "Open gacha history and browse pages", "ガチャ履歴を開いてページを送る", "가챠 기록 열고 페이지 넘기기");
            Add(d, "成就", "成就", "Achievements", "実績", "업적");
            Add(d, "打开成就各分组", "打開成就各分組", "Open each achievement group", "実績の各グループを開く", "각 업적 그룹 열기");
            Add(d, "图鉴", "圖鑑", "Compendium", "図鑑", "도감");
            Add(d, "打开图鉴界面", "打開圖鑑介面", "Open the compendium", "図鑑画面を開く", "도감 화면 열기");
            Add(d, "探索与收集", "探索與收集", "Exploration and collecting", "探索・収集", "탐험 및 수집");
            Add(d, "打开探索、收藏界面", "打開探索、收藏介面", "Open exploration and collections", "探索・コレクション画面を開く", "탐험 및 컬렉션 열기");
            Add(d, "好友列表", "好友清單", "Friends", "フレンド一覧", "친구 목록");
            Add(d, "打开好友界面", "打開好友介面", "Open friends", "フレンド画面を開く", "친구 화면 열기");
            Add(d, "档案与生活技能", "檔案與生活技能", "Profile and life skills", "プロフィール・生活スキル", "프로필 및 생활 스킬");
            Add(d, "打开档案、生活技能界面", "打開檔案、生活技能介面", "Open profile and life skills", "プロフィール・生活スキルを開く", "프로필 및 생활 스킬 열기");
            Add(d, "钓鱼与图鉴", "釣魚與圖鑑", "Fishing & compendium", "釣り・図鑑", "낚시 및 도감");
            Add(d, "打开钓鱼界面，钓一条鱼并确认进包", "開啟釣魚介面，釣一條魚並確認進包", "Open fishing, catch 1 fish, check bag", "釣り画面を開き魚を1匹釣って確認", "낚시 열고 물고기 1마리 낚기");
            Add(d, "采集与图鉴", "採集與圖鑑", "Gathering & compendium", "採集・図鑑", "채집 및 도감");
            Add(d, "打开采集界面，采集一处资源并确认进包", "開啟採集介面，採集一處資源並確認進包", "Open gathering, harvest 1 node, check bag", "採集画面を開き資源を1回採集", "채집 열고 자원 1곳 채집하기");

            // Mode descriptions, buttons, headers
            Add(d, "Steam 客户端 · 独立单机存档", "獨立存檔 B · 支援全區服客戶端", "Save slot B · All channels supported", "別セーブ B · 全サーバー対応", "별도 저장 B · 전 서버 지원");
            Add(d, "国际服单机 · Steam 客户端 · 英文界面 · 独立数据目录", "獨立存檔 B · 支援 B服 / TapTap / Steam", "Save slot B · Bilibili / TapTap / Steam supported", "ローカルワールド B · Bilibili / TapTap / Steam 対応", "로컬 월드 B · Bilibili / TapTap / Steam 지원");
            Add(d, "国服单机 · 官方中文客户端 · 独立数据目录", "單機存檔 A · 支援 B服 / TapTap / Steam", "Solo save A · Bilibili / TapTap / Steam supported", "ローカルワールド A · Bilibili / TapTap / Steam 対応", "로컬 월드 A · Bilibili / TapTap / Steam 지원");
            Add(d, "已切换到国际服（Steam），使用独立的英文存档。", "已切換到獨立存檔槽 B，請確認所選區服與用戶端目錄。", "Switched to save slot B. Any channel client can launch here.", "ローカルセーブ B に切り替えました。", "저장 슬롯 B로 전환했습니다.");
            Add(d, "已切换到单机，使用本电脑的独立单机存档。", "已切換到單機存檔槽 A，使用本電腦的獨立存檔。", "Switched to solo save slot A on this PC.", "ソロセーブ A に切り替えました。", "솔로 저장 슬롯 A로 전환했습니다.");
            Add(d, "已切换到局域网服务端，使用独立联机存档。", "已切換到區域網路服務端，使用獨立連線存檔。", "Switched to the LAN server using the isolated co-op save.", "LAN サーバー（専用マルチセーブ）に切り替えました。", "LAN 서버(독립 멀티 저장)로 전환했습니다.");
            Add(d, " SDK，但未找到已验证的 QuickSDK PC 接入布局。\n", " SDK，但未找到已驗證的 QuickSDK PC 接入佈局。\n", " SDK, but no verified QuickSDK PC layout was found.\n", " SDK が検出されましたが、検証済みの QuickSDK PC 構成が見つかりません。\n", " SDK가 감지되었으나 검증된 QuickSDK PC 구성을 찾지 못했습니다.\n");
            Add(d, " · PktMon 已记录 ", " · PktMon 已記錄 ", " · PktMon recorded ", " · PktMon 記録済み ", " · PktMon 기록 ");
            Add(d, "1. 管理员运行，选择公服原版 EXE；不要选择私服副本\n2. 开始采集并登录，按清单打开缺少的界面并等待加载\n3. 结束后核对完整清单并另存 ZIP；入库可取消，私人数据勿公开", "1. 管理員執行，選擇任意區服原版 EXE 或 Steam 捷徑；勿選私服副本\n2. 開始採集並登入，按清單打開缺少的介面並等待載入\n3. 結束後核對完整清單並另存 ZIP；入庫可取消，私人資料勿公開", "1. Run as administrator and pick any official client (Bilibili / TapTap / Steam EXE or .url)\n2. Start the capture and sign in; open missing screens from the checklist\n3. Finish capture, check the full checklist and save the ZIP; keep private data private", "1. 管理者として実行し、任意の公式クライアント（Bilibili / TapTap / Steam）を選択\n2. 収集を開始してログインし、チェックリストの各画面を開いて読み込み待ち\n3. 終了後リストを確認して ZIP を保存してください（個人データは非公開）", "1. 관리자 권한으로 실행 후 공식 클라이언트(Bilibili / TapTap / Steam) 선택\n2. 수집 시작 후 로그인하여 체크리스트 항목 화면들을 열고 로딩 대기\n3. 완료 후 목록을 확인하고 ZIP을 저장하세요 (개인 데이터 비공개)");
            Add(d, "127.0.0.1（仅本机）", "127.0.0.1（僅本機）", "127.0.0.1 (this PC only)", "127.0.0.1（ローカルのみ）", "127.0.0.1 (이 PC만)");
            Add(d, "O V E R F I E L D  /  本 地 世 界", "O V E R F I E L D  /  本 地 世 界", "O V E R F I E L D  /  L O C A L  W O R L D", "O V E R F I E L D  /  L O C A L  W O R L D", "O V E R F I E L D  /  L O C A L  W O R L D");
            Add(d, "Steam 正在启动游戏；如果它弹出登录或更新窗口，请先完成，进入游戏后开始加载要保存的界面。", "Steam 正在啟動遊戲；若出現登入或更新視窗請先完成，進入遊戲後再打開要保存的介面。", "Steam is starting the game. If a sign-in or update window appears, finish it first, then open the screens you want to save once you are in.", "Steam でゲームを起動中です。ログインや更新画面が出たら先に完了し、ゲーム内で保存したい画面を開いてください。", "Steam에서 게임을 실행 중입니다. 로그인/업데이트 창이 뜨면 먼저 완료한 뒤 게임 내에서 저장할 화면을 여세요.");
            Add(d, "UID 与房间网关", "UID 與房間閘道", "UID and room gateway", "UID・ルームゲートウェイ", "UID 및 룸 게이트웨이");
            Add(d, "UID 冲突：", "UID 衝突：", "UID conflict: ", "UID 競合：", "UID 충돌: ");
            Add(d, "UID 本地登录\n\n数据保存在本电脑\n支持各自导入与备份", "UID 本地登入\n\n資料儲存在本機\n支援各自匯入與備份", "Local UID sign-in\n\nData stays on this PC\nEach UID imports and backs up on its own", "ローカル UID ログイン\n\nデータは PC 内に保存\nUID 別に取込・バックアップ", "로컬 UID 로그인\n\n데이터는 이 PC에 저장\nUID별 가져오기 및 백업 지원");
            Add(d, "你的世界，从这里出发。", "你的世界，從這裡出發。", "Your world starts here.", "ここから、あなたの世界が始まる。", "당신의 세계가 여기서 시작됩니다.");
            Add(d, "保存", "儲存", "Save", "保存", "저장");
            Add(d, "保存账号 ZIP", "儲存 ZIP", "Save ZIP", "ZIP保存", "ZIP 저장");
            Add(d, "保存采集账号 ZIP（交付文件）", "儲存採集帳號 ZIP（交付檔案）", "Save the captured account ZIP (deliverable)", "収集済みアカウント ZIP の保存先", "수집한 계정 ZIP 저장");
            Add(d, "停止服务", "停止服務", "Stop services", "サービス停止", "서비스 중지");
            Add(d, "人数上限", "人數上限", "Capacity", "定員", "최대 인원");
            Add(d, "仅启动服务", "僅啟動服務", "Services", "サーバーのみ", "서버만 시작");
            Add(d, "仅本机连接 · 独立单机存档", "僅本機連線 · 支援全區服客戶端", "This PC only · All channels supported", "ローカル・全サーバー対応", "로컬 · 전 서버 지원");
            Add(d, "仅本机连接\n独立数据与存档", "僅本機連線\n獨立資料與存檔", "This PC only\nIsolated data and saves", "ローカル専用\n独立データ・セーブ", "로컬 전용\n독립 데이터 및 저장");
            Add(d, "代理设置未接入", "代理設定未接入", "Proxy not attached", "プロキシ未接続", "프록시 미연결");
            Add(d, "以自己的 UID 开始\n数据只保存在本电脑\n请勿公开私人存档", "以自己的 UID 開始\n資料只保存在本機\n請勿公開私人存檔", "Start with your own UID\nData stays on this PC\nNever publish private saves", "自分の UID で開始\nデータはこの PC にのみ保存\n個人データは非公開", "자신의 UID로 시작\n데이터는 이 PC에만 저장\n개인 데이터 공개 금지");
            Add(d, "公服启动器 / Steam 快捷方式|*.exe;*.url|可执行文件|*.exe|Steam 快捷方式|*.url", "公服啟動器 / Steam 捷徑|*.exe;*.url|可執行檔|*.exe|Steam 捷徑|*.url", "Official launcher / Steam shortcut|*.exe;*.url|Executable|*.exe|Steam shortcut|*.url", "公式ランチャー / Steam ショートカット|*.exe;*.url|実行ファイル|*.exe|Steam ショートカット|*.url", "공식 런처 / Steam 바로가기|*.exe;*.url|실행 파일|*.exe|Steam 바로가기|*.url");
            Add(d, "公服启动器路径已独立保存，私服游戏目录未改变。", "公服啟動器路徑已獨立儲存，私服遊戲目錄未改變。", "The official launcher path is stored separately; the private game folder is unchanged.", "公式ランチャーのパスを保存しました（私服フォルダは未変更）。", "공식 런처 경로를 별도 저장했습니다(사설 폴더 미변경).");
            Add(d, "公服启动器路径（仅公服，与私服目录独立）", "公服啟動器路徑（僅公服，與私服目錄獨立）", "Official launcher path (Bilibili / TapTap / Steam)", "公式クライアントパス（Bilibili / TapTap / Steam）", "공식 클라이언트 경로 (Bilibili / TapTap / Steam)");
            Add(d, "公服账号采集", "公服帳號採集", "Official account capture", "公式アカウントデータ収集", "공식 계정 데이터 수집");
            Add(d, "公服路径不可用", "公服路徑不可用", "Official path unavailable", "パス無効", "경로 사용 불가");
            Add(d, "公服采集", "公服採集", "Official capture", "公式データ収集", "공식 서버 수집");
            Add(d, "公服采集操作失败。", "公服採集操作失敗。", "The official capture operation failed.", "データ収集操作に失敗しました。", "공식 수집 작업에 실패했습니다.");
            Add(d, "关闭房间", "關閉房間", "Close room", "ルーム閉鎖", "방 닫기");
            Add(d, "切换失败：", "切換失敗：", "Switch failed: ", "切替失敗：", "전환 실패: ");
            Add(d, "创建房间", "建立房間", "Create room", "ルーム作成", "방 만들기");
            Add(d, "删除房间", "刪除房間", "Delete room", "ルーム削除", "방 삭제");
            Add(d, "刷新", "重新整理", "Refresh", "更新", "새로고침");
            Add(d, "单机", "單機", "Solo", "ソロ", "솔로");
            Add(d, "单机启动时选择客户端目录", "選擇任意區服（B服 / TapTap / Steam）用戶端目錄", "Select client folder (Bilibili / TapTap / Steam)", "クライアントフォルダを選択 (Bilibili / TapTap / Steam)", "클라이언트 폴더 선택 (Bilibili / TapTap / Steam)");
            Add(d, "单机存档  ·  本地保存 / 独立备份", "單機存檔  ·  本地保存 / 獨立備份", "Solo save  ·  stored locally / backed up separately", "ソロセーブ · ローカル保存 / 個別バックアップ", "솔로 저장 · 로컬 저장 / 개별 백업");
            Add(d, "单机模式", "單機模式", "Single player", "ソロモード", "싱글 모드");
            Add(d, "单机模式：请选择本地客户端目录", "請選擇本機客戶端目錄（支援 B服 / TapTap / Steam）", "Choose local client folder (Bilibili / TapTap / Steam)", "クライアントフォルダを選択 (Bilibili / TapTap / Steam)", "로컬 클라이언트 폴더 선택 (Bilibili / TapTap / Steam)");
            Add(d, "占位字段 ", "佔位欄位 ", "Placeholder fields ", "仮フィールド ", "임시 필드 ");
            Add(d, "发布局域网服务  →", "發佈區域網路服務  →", "Publish to LAN  →", "LAN サービス開始  →", "LAN 서버 시작  →");
            Add(d, "发布局域网服务 · 独立联机存档", "發佈區域網路服務 · 全區服連線", "Publish to LAN · isolated co-op save", "LAN マルチ · 全サーバー対応", "LAN 멀티 · 전 서버 지원");
            Add(d, "发布局域网服务\n独立联机存档", "發佈區域網路服務\n獨立連線存檔", "Publish to LAN\nIsolated co-op save", "LAN サービス\n専用マルチセーブ", "LAN 서비스\n독립 멀티 저장");
            Add(d, "取消操作", "取消操作", "Cancel", "キャンセル", "작업 취소");
            Add(d, "只有成功解码的公服响应才算已采集；快照暂不可读会显示暂时无法判断，结束后按完整数据核对。", "只有成功解碼的公服回應才算已採集；快照暫不可讀會顯示暫時無法判斷，結束後按完整資料核對。", "Only official responses that decoded cleanly count as collected. An unreadable snapshot shows as undetermined, so check the full data when you finish.", "正常に復号された公式応答のみ「収集済み」と表示されます。終了時に全データで最終判定します。", "정상 디코딩된 공식 서버 응답만 수집됨으로 표시됩니다. 종료 후 전체 데이터로 최종 확인됩니다.");
            Add(d, "可随时退出 · 游戏与服务独立运行 · 两种模式的存档分开保存", "可隨時退出 · 遊戲與服務獨立運行 · 各存檔互不干擾", "Close any time · the game and services run independently · saves stay isolated", "いつでも終了可 · ゲームとサービスは独立 · 各セーブデータは分離", "언제든 종료 가능 · 게임과 서비스 독립 실행 · 각 저장 슬롯 분리");
            Add(d, "名称", "名稱", "Name", "名前", "이름");
            Add(d, "启动单机游戏  →", "啟動單機遊戲  →", "Start solo game  →", "ソロゲーム起動  →", "솔로 게임 시작  →");
            Add(d, "启动器已就绪，正在读取本地服务状态。", "啟動器已就緒，正在讀取本地服務狀態。", "Launcher ready; reading local service status.", "ランチャー準備完了。ローカルサービス状態を取得中…", "런처 준비 완료. 로컬 서비스 상태 확인 중…");
            Add(d, "启动服务", "啟動服務", "Start", "サービス開始", "서버 시작");
            Add(d, "启动游戏  →", "啟動遊戲  →", "Start game  →", "ゲーム起動  →", "게임 시작  →");
            Add(d, "启动设置", "啟動設定", "Launch settings", "起動設定", "실행 설정");
            Add(d, "响应", "回應", "Responses", "応答数", "응답");
            Add(d, "在线", "線上", "Online", "オンライン", "온라인");
            Add(d, "在线玩家 ", "線上玩家 ", "Online players ", "オンラインプレイヤー ", "접속 플레이어 ");
            Add(d, "地址", "位址", "Address", "アドレス", "주소");
            Add(d, "场景", "場景", "Scene", "シーン", "씬");
            Add(d, "场景（0 为默认）", "場景（0 為預設）", "Scene (0 is the default)", "シーン（0 = 既定）", "씬 (0 = 기본값)");
            Add(d, "备份存档", "備份存檔", "Backup", "保存", "백업");
            Add(d, "备份已保存：", "備份已儲存：", "Backup saved: ", "バックアップ保存済み：", "백업 저장됨: ");
            Add(d, "官方客户端采集账号数据", "支援 B服 / TapTap / Steam 採集", "Official client capture (all channels)", "公式データ収集（全対応）", "공식 데이터 수집 (전 서버)");
            Add(d, "○  等待启动", "○  等待啟動", "○  Waiting to start", "○  起動待ち", "○  실행 대기");
            Add(d, "●  可以开始游戏", "●  可以開始遊戲", "●  Ready to play", "●  起動可能", "●  플레이 가능");
            Add(d, "●  局域网已发布", "●  區域網路已發佈", "●  Published to LAN", "●  LAN 公開中", "●  LAN 공개됨");
            Add(d, "●  游戏已运行", "●  遊戲已運行", "●  Game running", "●  ゲーム起動中", "●  게임 실행 중");
            Add(d, "\n等级 ", "\n等級 ", "\nLevel ", "\nレベル ", "\n레벨 ");
            Add(d, "\n请选择包含 LoloServer 的上层目录，或把服务端放到启动器旁边的 server-kit 文件夹。", "\n請選擇包含 LoloServer 的上層目錄，或把服務端放到啟動器旁邊的 server-kit 資料夾。", "\nPick the folder that contains LoloServer, or put the server into the server-kit folder next to this launcher.", "\nLoloServer を含む上位フォルダを選ぶか、ランチャー横の server-kit フォルダに配置してください。", "\nLoloServer가 포함된 상위 폴더를 선택하거나 런처 옆 server-kit 폴더에 넣어 주세요.");
            Add(d, "。可用该 UID 登录当前模式。", "。可用該 UID 登入目前模式。", ". You can sign in with this UID in the current mode.", "。この UID で現在モードにログインできます。", ". 이 UID로 현재 모드에 로그인할 수 있습니다.");
            Add(d, "。可私下交给可信服主，入库为可选操作。", "。可私下交給可信服主，入庫為可選操作。", ". Hand it privately to a trusted host; importing is optional.", "。信頼できるホストへ私的に共有できます（DB登録は任意）。", ". 신뢰할 수 있는 호스트에게 비공개로 전달할 수 있습니다(DB 가져오기는 선택 사항).");
            Add(d, "。采集时将通过 Steam 启动，需先登录 Steam。", "。採集時將透過 Steam 啟動，需先登入 Steam。", ". Capture starts it through Steam, so sign in to Steam first.", "。収集時は Steam 経由で起動するため事前に Steam へログインしてください。", "。수집 시 Steam을 통해 실행되므로 먼저 Steam에 로그인하세요.");
            Add(d, "导入报告位于数据目录 accounts\\", "匯入報告位於資料目錄 accounts\\", "The import report is under the data folder, accounts\\", "インポート結果はデータフォルダ配下 accounts\\", "가져오기 보고서 위치: 데이터 폴더 내 accounts\\");
            Add(d, "导入数据", "匯入資料", "Import", "取込", "가져오기");
            Add(d, "导入未完成：", "匯入未完成：", "Import did not finish: ", "インポート未完了：", "가져오기 미완료: ");
            Add(d, "尚未启动 · 独立服务", "尚未啟動 · 獨立服務", "Not started · isolated service", "未起動 · 独立サービス", "미실행 · 독립 서비스");
            Add(d, "局域网", "區域網路", "LAN", "LAN", "LAN");
            Add(d, "局域网地址 · ", "區網位址 · ", "LAN address · ", "LAN アドレス · ", "LAN 주소 · ");
            Add(d, "局域网房间与玩家", "區域網路房間與玩家", "LAN rooms and players", "LAN ルーム・プレイヤー", "LAN 방 및 플레이어");
            Add(d, "局域网服务", "區域網路服務", "LAN server", "LAN マルチ", "LAN 멀티");
            Add(d, "局域网服务端 · 按 UID 管理 · 独立联机存档", "區網服務端 · 按 UID 管理 · 支援全區服客戶端", "LAN server · managed per UID · isolated co-op save", "LAN サーバー · UID 別管理 · 全サーバー対応", "LAN 서버 · UID별 관리 · 전 서버 지원");
            Add(d, "局域网权限", "區網權限", "LAN access", "LAN許可", "LAN 허용");
            Add(d, "局域网权限未设置，请允许 Windows 管理员确认后重试。", "區網權限未設定，請允許 Windows 管理員確認後重試。", "LAN access is not configured. Approve the Windows administrator prompt and retry.", "LAN アクセス権限が未設定です。管理者プロンプトを許可して再試行してください。", "LAN 접근 권한이 설정되지 않았습니다. 관리자 승인을 허용하고 다시 시도하세요.");
            Add(d, "已允许同一局域网访问当前服务。", "已允許同一區網存取目前服務。", "The same LAN can now reach this service.", "同じ LAN からのアクセスを許可しました。", "같은 LAN에서의 접속을 허용했습니다.");
            Add(d, "已入库：UID ", "已入庫：UID ", "Imported: UID ", "データベース登録完了：UID ", "DB 등록 완료: UID ");
            Add(d, "已取消另存；原账号ZIP保留，可稍后点击「保存账号 ZIP」。", "已取消另存；原帳號ZIP保留，可稍後點擊「儲存 ZIP」。", "Save cancelled. The account ZIP is kept; click Save ZIP later.", "保存をキャンセルしました。元 ZIP は保持されているため後で保存できます。", "저장을 취소했습니다. 원본 ZIP이 유지되므로 나중에 저장할 수 있습니다.");
            Add(d, "已导入 UID ", "已匯入 UID ", "Imported UID ", "インポート完了 UID ", "가져오기 완료 UID ");
            Add(d, "已就绪 · 独立账号与房间管理", "已就緒 · 獨立帳號與房間管理", "Ready · isolated accounts and rooms", "準備完了 · アカウント/ルーム管理", "준비 완료 · 계정 및 룸 관리");
            Add(d, "已就绪 · 等待游戏连接", "已就緒 · 等待遊戲連線", "Ready · waiting for the game", "準備完了 · ゲーム接続待ち", "준비 완료 · 게임 연결 대기");
            Add(d, "已识别 Steam 客户端（App ", "已識別 Steam 用戶端（App ", "Detected the Steam client (App ", "Steam クライアント検出 (App ", "Steam 클라이언트 감지됨 (App ");
            Add(d, "已连接 · 本地游戏线路", "已連線 · 本地遊戲線路", "Connected · local game route", "接続済み · ローカルルート", "연결됨 · 로컬 경로");
            Add(d, "开始采集", "開始採集", "Capture", "収集開始", "수집 시작");
            Add(d, "开放房间", "開放房間", "Open room", "ルーム公開", "방 열기");
            Add(d, "开放空间", "開放空間", "OverField", "OverField", "OverField");
            Add(d, "开放空间 · 本地启动器", "開放空間 · 本地啟動器", "OverField · Local Launcher", "OverField · ローカルランチャー", "OverField · 로컬 런처");
            Add(d, "开放空间启动器", "開放空間啟動器", "OverField Launcher", "OverField ランチャー", "OverField 런처");
            Add(d, "开放空间私服启动器", "開放空間私服啟動器", "OverField Launcher", "OverField ランチャー", "OverField 런처");
            Add(d, "当前模式的服务已停止，原网络路线已恢复。", "當前模式的服務已停止，原網路路線已恢復。", "This mode's services stopped and the previous network route was restored.", "現在のサービスを停止し、元のネットワーク設定を復元しました。", "현재 모드의 서비스를 중지하고 기존 네트워크 경로를 복원했습니다.");
            Add(d, "房间", "房間", "Room", "ルーム", "방");
            Add(d, "房间与玩家", "房間與玩家", "Rooms", "ルーム一覧", "방 목록");
            Add(d, "房间与玩家    ·    ", "房間與玩家    ·    ", "Rooms and players    ·    ", "ルーム・プレイヤー    ·    ", "방 및 플레이어    ·    ");
            Add(d, "房间名称", "房間名稱", "Room name", "ルーム名", "방 이름");
            Add(d, "所选目录缺少服务端：", "所選目錄缺少服務端：", "The selected folder has no server: ", "選択したフォルダにサーバーがありません：", "선택한 폴더에 서버가 없습니다: ");
            Add(d, "所选目录里没有找到游戏客户端 launcher.exe。\n请选择包含 OverField Game 或 launcher.exe 的目录。", "所選目錄裡沒有找到遊戲用戶端 launcher.exe。\n請選擇包含 OverField Game 或 launcher.exe 的目錄。", "No game client launcher.exe was found in the selected folder.\nPick the folder that contains OverField Game or launcher.exe.", "選択したフォルダ内に launcher.exe が見つかりません。\nOverField Game または launcher.exe を含むフォルダを選択してください。", "선택한 폴더에서 launcher.exe를 찾을 수 없습니다.\nOverField Game 또는 launcher.exe가 포함된 폴더를 선택하세요.");
            Add(d, "打开日志文件夹", "打開日誌資料夾", "Open log folder", "ログを開く", "로그 폴더 열기");
            Add(d, "抓包就绪后会打开公服。结束采集将生成账号 ZIP，并让你选择保存位置；入库是可选操作，取消或冲突不影响已交付的 ZIP。", "抓包就緒後會打開所選公服。結束採集將生成帳號 ZIP，並讓你選擇儲存位置；入庫為可選操作，取消或衝突不影響已交付的 ZIP。", "The selected official client opens once capture is ready. Finishing capture builds an account ZIP and asks where to save it; importing is optional.", "収集開始後、選択した公式クライアントを起動します。終了時にアカウント ZIP を生成し保存先を選択します（ローカルDBへの取込は任意）。", "캡처가 준비되면 선택한 공식 클라이언트가 열립니다. 종료 후 계정 ZIP이 생성되며 로컬 DB 가져오기는 선택 사항입니다.");
            Add(d, "抓包已就绪，已启动配置的公服启动器。公服路径未使用私服副本或本地UID。", "抓包已就緒，已啟動配置的公服啟動器。公服路徑未使用私服副本或本地UID。", "Capture ready; the configured official launcher started. The official path uses no private copy and no local UID.", "収集準備完了。公式クライアントを起動しました（私服コピー・ローカルUID不使用）。", "캡처 준비 완료. 공식 클라이언트를 실행했습니다(사설 사본/로컬 UID 미사용).");
            Add(d, "抓包已就绪，正在通过 Steam 启动客户端。公服路径未使用私服副本或本地UID。", "抓包已就緒，正在透過 Steam 啟動用戶端。公服路徑未使用私服副本或本地UID。", "Capture ready; launching the client through Steam. The official path uses no private copy and no local UID.", "収集準備完了。Steam 経由でクライアントを起動しています。", "캡처 준비 완료. Steam을 통해 클라이언트를 실행하고 있습니다.");
            Add(d, "抓包已开始，公服启动器已打开。请正常登录并进入场景；若客户端已在运行，请退出后重新打开以采集登录响应。", "抓包已開始，公服啟動器已打開。請正常登入並進入場景；若用戶端已在運行，請退出後重新打開以採集登入回應。", "Capture started and official client opened. Sign in and enter a scene; if the client was already open, restart it to capture login.", "収集を開始し公式クライアントを起動しました。ログインしてマップに入ってください（既に起動中の場合は再起動してください）。", "수집이 시작되고 공식 클라이언트가 열렸습니다. 로그인 후 맵에 진입하세요(이미 실행 중이면 재시작하세요).");
            Add(d, "操作失败：", "操作失敗：", "Operation failed: ", "操作失敗：", "작업 실패: ");
            Add(d, "操作已取消。已启动的服务会保留，请查看状态后重试。", "操作已取消。已啟動的服務會保留，請查看狀態後重試。", "Cancelled. Services that already started stay up; check the status and retry.", "キャンセルしました。既に起動したサービスはそのまま維持されます。", "작업을 취소했습니다. 이미 시작된 서비스는 유지됩니다.");
            Add(d, "操作未完成：", "操作未完成：", "Operation unfinished: ", "操作未完了：", "작업 미완료: ");
            Add(d, "操作超时，已停止等待。请检查状态后重试。", "操作逾時，已停止等待。請檢查狀態後重試。", "The operation timed out and is no longer being waited on. Check the status and retry.", "タイムアウトしました。状態を確認して再試行してください。", "작업 시간이 초과되었습니다. 상태를 확인한 후 다시 시도하세요.");
            Add(d, "数据不完整，确认导入", "資料不完整，確認匯入", "Import anyway", "不完全でも取り込む", "불완전한 상태로 가져오기");
            Add(d, "数据目录", "資料目錄", "Data", "データ", "데이터 폴더");
            Add(d, "文件已保存，但无法打开所在位置：", "檔案已儲存，但無法打開所在位置：", "The file was saved, but its folder could not be opened: ", "ファイルを保存しましたがフォルダを開けませんでした：", "파일이 저장되었지만 폴더를 열 수 없습니다: ");
            Add(d, "断开玩家", "斷開玩家", "Disconnect player", "切断", "연결 끊기");
            Add(d, "新房间", "新房間", "New room", "新規ルーム", "새 방");
            Add(d, "无法完成操作：", "無法完成操作：", "Could not finish the operation: ", "操作を完了できませんでした：", "작업을 완료하지 못했습니다: ");
            Add(d, "无法打开文件夹：", "無法打開資料夾：", "Could not open the folder: ", "フォルダを開けません：", "폴더를 열 수 없습니다: ");
            Add(d, "无法检查，请查看运行记录", "無法檢查，請查看運行記錄", "Cannot check; see the run log", "確認不可（ログ参照）", "확인 불가(로그 참조)");
            Add(d, "无法读取服务状态。", "無法讀取服務狀態。", "Could not read the service status.", "サービス状態を取得できませんでした。", "서비스 상태를 읽을 수 없습니다.");
            Add(d, "更改目录", "變更目錄", "Browse", "変更...", "변경...");
            Add(d, "最近核对：", "最近核對：", "Last check: ", "最終確認：", "최근 확인: ");
            Add(d, "检测到抓包缺口或未解析的流尾部，备份可能不完整；仍可保存已解码账号 ZIP。", "偵測到封包缺口或未解析的流尾部，備份可能不完整；仍可儲存已解碼帳號 ZIP。", "A TCP capture gap or undecoded stream tail was found. The backup may be incomplete; the decoded account ZIP can still be saved.", "TCP パケット欠落または未解析末尾が検出されました。不完全な可能性がありますが ZIP の保存は可能です。", "TCP 패킷 누락 또는 미해석 끝부분이 감지되었습니다. 백업이 불완전할 수 있으나 ZIP 저장은 가능합니다.");
            Add(d, "服务地址", "服務位址", "Server address", "接続アドレス", "서버 주소");
            Add(d, "服务控制程序未返回结果，请查看运行记录。", "服務控制程式未回傳結果，請查看運行記錄。", "The service controller returned nothing; see the run log.", "サービスコントローラーから応答がありません。", "서비스 제어 프로그램이 응답하지 않았습니다.");
            Add(d, "服务端", "服務端", "Server", "サーバー", "서버");
            Add(d, "未启动", "未啟動", "Not started", "未起動", "미실행");
            Add(d, "未找到 Python，请在 launcher.settings.json 中设置 pythonExe。", "未找到 Python，請在 launcher.settings.json 中設定 pythonExe。", "Python was not found. Set pythonExe in launcher.settings.json.", "Python が見つかりません。", "Python을 찾을 수 없습니다.");
            Add(d, "未获得账号数据，可重新采集。", "未獲得帳號資料，可重新採集。", "No account data was obtained; capture again.", "アカウントデータを取得できませんでした。再度収集してください。", "계정 데이터를 얻지 못했습니다. 다시 수집해 주세요.");
            Add(d, "未采集时的操作建议", "未採集時的操作建議", "What to do about missing items", "未取得時の操作ガイド", "미수집 시 안내");
            Add(d, "本 地 世 界", "本 地 世 界", "L O C A L  W O R L D", "L O C A L  W O R L D", "L O C A L  W O R L D");
            Add(d, "本地 UID 与房间管理", "本地 UID 與房間管理", "Local UID and room management", "ローカル UID・ルーム管理", "로컬 UID 및 방 관리");
            Add(d, "本地服务与游戏线路已就绪。", "本地服務與遊戲線路已就緒。", "Local services and the game route are ready.", "ローカルサービスとゲーム経路の準備ができました。", "로컬 서비스 및 게임 경로가 준비되었습니다.");
            Add(d, "本机单机 · 按 UID 登录 · 独立数据目录", "本機單機 · 按 UID 登入 · 獨立資料目錄", "This PC · sign in by UID · isolated data folder", "PC ローカル · UID ログイン", "PC 로컬 · UID 로그인");
            Add(d, "权限已配置", "權限已配置", "Access configured", "権限設定済", "권한 설정됨");
            Add(d, "查看在线玩家与房间", "查看線上玩家與房間", "View online players and rooms", "オンラインプレイヤー管理", "접속 플레이어 및 방 관리");
            Add(d, "检查中", "檢查中", "Checking", "確認中", "확인 중");
            Add(d, "检测到 ", "偵測到 ", "Detected ", "検出：", "감지됨: ");
            Add(d, "欢迎回到开放空间。\n选择运行模式，设置自己的 UID，然后开始你的世界。\n此预览未连接官服、未启动游戏或服务。", "歡迎回到開放空間。\n選擇運行模式，設定自己的 UID，然後開始你的世界。\n此預覽未連接官服、未啟動遊戲或服務。", "Welcome back to OverField.\nPick a run mode, set your own UID, then start your world.\nThis preview is not connected to the official server and starts no game or service.", "OverField ローカルランチャーへようこそ。\n実行モード・区服・UID を設定してゲームを開始できます（これはプレビューです）。", "OverField 로컬 런처에 오신 것을 환영합니다.\n실행 모드, 서버, UID를 설정해 게임을 시작하세요(미리보기).");
            Add(d, "正在停止当前模式的服务并恢复原网络路线。", "正在停止當前模式的服務並恢復原網路路線。", "Stopping this mode's services and restoring the previous network route.", "現在のサービスを停止してネットワーク設定を復元中…", "현재 서비스 중지 및 기존 네트워크 경로 복원 중…");
            Add(d, "正在停止服务", "正在停止服務", "Stopping services", "停止処理中", "서비스 중지 중");
            Add(d, "正在准备 UID ", "正在準備 UID ", "Preparing UID ", "UID を準備中 ", "UID 준비 중 ");
            Add(d, "正在准备游戏", "正在準備遊戲", "Preparing the game", "ゲーム準備中", "게임 준비 중");
            Add(d, "正在切换运行模式", "正在切換運行模式", "Switching the run mode", "モード切替中", "모드 전환 중");
            Add(d, "正在另存账号 ZIP", "正在另存帳號 ZIP", "Saving the account ZIP", "ZIP を保存中", "계정 ZIP 저장 중");
            Add(d, "正在启动 ", "正在啟動 ", "Starting ", "起動中：", "시작 중: ");
            Add(d, "正在处理…", "正在處理…", "Working…", "処理中…", "처리 중…");
            Add(d, "正在备份独立存档…", "正在備份獨立存檔…", "Backing up the isolated save…", "バックアップを作成中…", "백업 생성 중…");
            Add(d, "正在导入角色资料", "正在匯入角色資料", "Importing character data", "キャラクターデータを取込中", "캐릭터 데이터 가져오는 중");
            Add(d, "正在校验公服启动器路径", "正在校驗公服啟動器路徑", "Checking the official launcher path", "公式パスを検証中", "공식 클라이언트 확인 중");
            Add(d, "正在检查", "正在檢查", "Checking", "確認中", "확인 중");
            Add(d, "正在确认抓包就绪并启动公服", "正在確認抓包就緒並啟動公服", "Waiting for capture, then starting the official client", "キャプチャ準備後に公式ゲームを起動中", "캡처 준비 후 공식 클라이언트 실행 중");
            Add(d, "正在结束采集并生成账号 ZIP", "正在結束採集並生成帳號 ZIP", "Finishing the capture and building the account ZIP", "収集を終了してアカウント ZIP を生成中", "수집 종료 및 계정 ZIP 생성 중");
            Add(d, "正在设置局域网权限", "正在設定區網權限", "Configuring LAN access", "LAN ファイアウォール設定中", "LAN 방화벽 설정 중");
            Add(d, "此版本尚未适配，不能仅凭 DLL 名称推断登录方式。\n", "此版本尚未適配，不能僅憑 DLL 名稱推斷登入方式。\n", "This build is not supported yet; the login method cannot be inferred from DLL names alone.\n", "このビルドは未対応です。DLL のみでログイン方式は判別できません。\n", "이 빌드는 아직 지원되지 않습니다. DLL 이름만으로 로그인 방식을 추정할 수 없습니다.\n");
            Add(d, "注意：包体解码不完整，装备与外观可能为空。", "注意：封包解碼不完整，裝備與外觀可能為空。", "Note: the package did not decode completely, so equipment and appearance may be empty.", "注意：データの復号が不完全なため装備や外見が空になる場合があります。", "주의: 패키지 디코딩이 불완전하여 장비와 외형이 비어 있을 수 있습니다.");
            Add(d, "游戏与服务独立运行  ·  单机 / 联机存档分开保存", "遊戲與服務獨立運行  ·  單機 / 連線存檔分開保存", "The game and the services run independently  ·  solo and co-op saves are kept apart", "ゲームとサービスは独立して動作 · 各セーブデータは分離保存", "게임과 서비스는 독립 실행 · 솔로 / 멀티 저장 데이터 분리");
            Add(d, "游戏已打开，使用本地 UID ", "遊戲已打開，使用本地 UID ", "Game is open with local UID ", "ローカル UID でゲームを起動しました：", "로컬 UID로 게임이 열렸습니다: ");
            Add(d, "游戏已经运行，已保留当前进度。", "遊戲已經運行，已保留當前進度。", "The game is already running; current progress was kept.", "ゲームは既に起動中です（現在の進行を保持）。", "게임이 이미 실행 중입니다(현재 진행 유지).");
            Add(d, "游戏目录已保存。", "遊戲目錄已儲存。", "Game folder saved.", "ゲームフォルダを保存しました。", "게임 폴더를 저장했습니다.");
            Add(d, "游戏网关", "遊戲閘道", "Game gateway", "ゲートウェイ", "게이트웨이");
            Add(d, "游戏连接", "遊戲連線", "Game link", "ゲーム接続", "게임 연결");
            Add(d, "状态", "狀態", "Status", "状態", "상태");
            Add(d, "状态检查失败：", "狀態檢查失敗：", "Status check failed: ", "状態確認失敗：", "상태 확인 실패: ");
            Add(d, "独立数据目录  ·  ", "獨立資料目錄  ·  ", "Isolated data folder  ·  ", "保存先フォルダ  ·  ", "독립 데이터 폴더  ·  ");
            Add(d, "用你自己的渠道账号登录官方客户端，采集到的数据只写入本机账号库。", "用你自己的區服帳號登入官方用戶端（支援 B服 / TapTap / Steam），採集到的資料只寫入本機。", "Sign in with your own channel account (Bilibili / TapTap / Steam); captured data stays local.", "ご自身のアカウント（Bilibili / TapTap / Steam）でログインしてください。収集データはローカルのみに保存されます。", "본인의 서버 계정(Bilibili / TapTap / Steam)으로 로그인하세요. 수집 데이터는 로컬에만 저장됩니다.");
            Add(d, "界面预览", "介面預覽", "UI preview", "UI プレビュー", "UI 미리보기");
            Add(d, "登录中", "登入中", "Signing in", "ログイン中", "로그인 중");
            Add(d, "目录不可用", "目錄不可用", "Folder unavailable", "フォルダ無効", "폴더 사용 불가");
            Add(d, "确认入库", "確認入庫", "Confirm import", "インポート確認", "DB 가져오기 확인");
            Add(d, "确认导入资料", "確認匯入資料", "Confirm import", "インポート確認", "데이터 가져오기 확인");
            Add(d, "移入房间", "移入房間", "Move to room", "ルームへ移動", "방으로 이동");
            Add(d, "程序已经退出，但输出流未结束，已停止等待。", "程式已經退出，但輸出流未結束，已停止等待。", "The program exited but its output stream never closed; no longer waiting.", "プロセス終了後も出力ストリームが閉じないため待機を終了しました。", "프로그램이 종료되었으나 출력 스트림이 닫히지 않아 대기를 중지했습니다.");
            Add(d, "等待发布局域网服务", "等待發佈區網服務", "Waiting to publish to LAN", "LAN 公開待ち", "LAN 서버 대기 중");
            Add(d, "等待服务启动", "等待服務啟動", "Waiting for the services", "サービス起動待ち", "서비스 시작 대기");
            Add(d, "等待核对", "等待核對", "Waiting to check", "確認待ち", "확인 대기");
            Add(d, "等待检查", "等待檢查", "Waiting to check", "確認待ち", "확인 대기");
            Add(d, "等待游戏连接", "等待遊戲連線", "Waiting for the game", "ゲーム接続待ち", "게임 연결 대기");
            Add(d, "等待网关启动", "等待閘道啟動", "Waiting for the gateway", "ゲートウェイ待ち", "게이트웨이 대기");
            Add(d, "等待连接", "等待連線", "Waiting to connect", "接続待ち", "연결 대기");
            Add(d, "管理接口仅在本机开放。", "管理介面僅在本機開放。", "The admin endpoint is open on this PC only.", "管理 API はこの PC のみで待ち受けています。", "관리 인터페이스는 이 PC에서만 열려 있습니다.");
            Add(d, "结束采集", "結束採集", "Finish", "収集終了", "수집 종료");
            Add(d, "缺少 official_capture.py，请重新解压完整工具包。", "缺少 official_capture.py，請重新解壓完整工具包。", "official_capture.py is missing; extract the complete package again.", "official_capture.py がありません。再展開してください。", "official_capture.py가 없습니다. 전체 패키지를 다시 압축 해제하세요.");
            Add(d, "缺少 official_import.py，请重新解压完整工具包。", "缺少 official_import.py，請重新解壓完整工具包。", "official_import.py is missing; extract the complete package again.", "official_import.py がありません。再展開してください。", "official_import.py가 없습니다. 전체 패키지를 다시 압축 해제하세요.");
            Add(d, "联机存档  ·  本地保存 / 独立备份", "連線存檔  ·  本地保存 / 獨立備份", "Co-op save  ·  stored locally / backed up separately", "マルチセーブ · ローカル保存 / 個別バックアップ", "멀티 저장 · 로컬 저장 / 개별 백업");
            Add(d, "角色名", "角色名", "Character name", "キャラクター名", "캐릭터 이름");
            Add(d, "设置人数", "設定人數", "Set capacity", "定員設定", "인원 설정");
            Add(d, "设置人数上限", "設定人數上限", "Set capacity", "定員上限を設定", "최대 인원 설정");
            Add(d, "请使用已验证的 QuickSDK PC 或 PCGameSDK 客户端版本，并查看分支兼容说明。", "請使用已驗證的 QuickSDK PC、Steam 或 PCGameSDK 用戶端版本。", "Use a verified PCGameSDK, QuickSDK PC, or Steam client build and read the branch compatibility notes.", "検証済みの PCGameSDK / QuickSDK PC / Steam クライアントを使用してください。", "검증된 PCGameSDK, QuickSDK PC 또는 Steam 클라이언트를 사용해 주세요.");
            Add(d, "请先退出游戏，再切换运行模式。", "請先退出遊戲，再切換運行模式。", "Close the game before switching the run mode.", "モードを切り替える前にゲームを終了してください。", "실행 모드를 전환하기 전에 게임을 먼저 종료하세요.");
            Add(d, "请先退出游戏，再导入角色数据。", "請先退出遊戲，再匯入角色資料。", "Close the game before importing character data.", "データを取り込む前にゲームを終了してください。", "데이터를 가져오기 전에 게임을 먼저 종료하세요.");
            Add(d, "请先选中房间或玩家。", "請先選中房間或玩家。", "Select a room or a player first.", "先にルームまたはプレイヤーを選択してください。", "먼저 방이나 플레이어를 선택하세요.");
            Add(d, "请选择公服启动器或未修改的原版客户端 EXE", "請選擇任意區服公服啟動器、原版 EXE 或 Steam 捷徑", "Select official launcher EXE or Steam .url (Bilibili / TapTap / Steam)", "公式クライアント EXE / Steam .url を選択 (Bilibili / TapTap / Steam)", "공식 클라이언트 EXE / Steam .url 선택 (Bilibili / TapTap / Steam)");
            Add(d, "账号 ZIP 已交付：", "帳號 ZIP 已交付：", "Account ZIP delivered: ", "アカウント ZIP を出力しました：", "계정 ZIP 저장 완료: ");
            Add(d, "账号 ZIP 已交付；已取消覆盖原有 UID 存档。", "帳號 ZIP 已交付；已取消覆蓋原有 UID 存檔。", "Account ZIP delivered; overwriting the existing UID save was cancelled.", "アカウント ZIP を保存しました（既存 UID 上書きはキャンセル）。", "계정 ZIP 저장됨(기존 UID 덮어쓰기는 취소됨).");
            Add(d, "账号 ZIP 已交付；已取消覆盖，原有存档保持不变。", "帳號 ZIP 已交付；已取消覆蓋，原有存檔保持不變。", "Account ZIP delivered; overwrite cancelled, so the existing save stayed unchanged.", "アカウント ZIP を保存しました。上書きはキャンセルされました。", "계정 ZIP 저장됨. 덮어쓰기가 취소되어 기존 저장이 유지됩니다.");
            Add(d, "账号 ZIP 已交付；游戏运行中，未覆盖原有存档。", "帳號 ZIP 已交付；遊戲運行中，未覆蓋原有存檔。", "Account ZIP delivered; existing save was not overwritten while the game is running.", "アカウント ZIP を保存しました（ゲーム起動中のため既存データは未上書き）。", "계정 ZIP 저장됨(게임 실행 중이므로 기존 데이터 미변경).");
            Add(d, "账号 ZIP 已保存交付。是否同时写入本机账号库？\nUID ", "帳號 ZIP 已保存交付。是否同時寫入本機帳號庫？\nUID ", "Account ZIP saved for delivery. Also write it into this machine's account database?\nUID ", "アカウント ZIP を保存しました。ローカルDBにも書き込みますか？\nUID ", "계정 ZIP을 저장했습니다. 로컬 계정 DB에도 기록하시겠습니까?\nUID ");
            Add(d, "账号 ZIP 已保存：", "帳號 ZIP 已保存：", "Account ZIP saved: ", "アカウント ZIP 保存済み：", "계정 ZIP 저장됨: ");
            Add(d, "账号 ZIP 已生成但尚未另存交付，可点击「保存账号 ZIP」重试。", "帳號 ZIP 已生成但尚未另存交付，可點擊「儲存 ZIP」重試。", "The account ZIP exists but was not saved for delivery; click Save ZIP to retry.", "アカウント ZIP は生成済みですが未保存です。「ZIP保存」を押してください。", "계정 ZIP이 생성되었으나 아직 저장되지 않았습니다. ZIP 저장을 눌러 주세요.");
            Add(d, "账号入库失败。", "帳號入庫失敗。", "Writing the account into the database failed.", "ローカルDBへの登録に失敗しました。", "계정 DB 등록에 실패했습니다.");
            Add(d, "账号入库", "帳號入庫", "Import account", "アカウント取込", "계정 가져오기");
            Add(d, "UID 冲突", "UID 衝突", "UID conflict", "UID 競合", "UID 충돌");
            Add(d, "账号备份 / 解码抓包 ZIP|*.zip", "帳號備份 / 解碼抓包 ZIP|*.zip", "Account backup / decoded capture ZIP|*.zip", "アカウントバックアップ / 復号済み ZIP|*.zip", "계정 백업 / 디코딩된 ZIP|*.zip");
            Add(d, "账号备份 ZIP|*.zip", "帳號備份 ZIP|*.zip", "Account backup ZIP|*.zip", "アカウントバックアップ ZIP|*.zip", "계정 백업 ZIP|*.zip");
            Add(d, "账号已写入数据库：UID ", "帳號已寫入資料庫：UID ", "Account written to the database: UID ", "データベースへ登録しました：UID ", "데이터베이스에 기록됨: UID ");
            Add(d, "运行中 · 本地服务正常", "運行中 · 本地服務正常", "Running · local services healthy", "稼働中 · ローカルサービス正常", "실행 중 · 로컬 서비스 정상");
            Add(d, "运行模式", "運行模式", "Run mode", "実行モード", "실행 모드");
            Add(d, "运行记录", "運行記錄", "Run log", "実行ログ", "실행 로그");
            Add(d, "返回游戏  →", "返回遊戲  →", "Back to game  →", "ゲームに戻る  →", "게임으로 돌아가기  →");
            Add(d, "这个包的解码不完整（", "這個包的解碼不完整（", "This package did not decode completely (", "このデータの復号は不完全です（", "이 패키지는 불완전하게 디코딩되었습니다(");
            Add(d, "选择你的世界", "選擇你的世界", "Choose your world", "ワールド選択", "월드 선택");
            Add(d, "选择公服启动器、原版客户端，或 Steam 游戏快捷方式", "選擇公服啟動器、原版用戶端，或 Steam 遊戲捷徑", "Select the official launcher, the original client, or a Steam shortcut", "公式ランチャー・元クライアント・Steam ショートカットを選択", "공식 런처, 원본 클라이언트 또는 Steam 바로가기 선택");
            Add(d, "选择启动器", "選擇啟動器", "Browse", "選択...", "찾아보기");
            Add(d, "选择游戏安装目录，单机启动时使用其中的 launcher.exe", "選擇遊戲安裝目錄（B服 / TapTap / Steam），啟動時使用其中的 launcher.exe", "Select game install folder (Bilibili / TapTap / Steam)", "ゲームフォルダを選択 (Bilibili / TapTap / Steam)", "게임 설치 폴더 선택 (Bilibili / TapTap / Steam)");
            Add(d, "选择账号备份或解码抓包 ZIP", "選擇帳號備份或解碼抓包 ZIP", "Select an account backup or a decoded capture ZIP", "アカウントバックアップまたは復号済み ZIP を選択", "계정 백업 또는 디코딩된 ZIP 선택");
            Add(d, "采集中：公服启动器已打开，请正常登录并加载要保存的界面，完成后点「结束采集」。", "採集中：公服啟動器已打開，請正常登入並載入要保存的介面，完成後點「結束採集」。", "Capturing: the official client is open. Sign in normally, load the screens you want to save, then click Finish.", "収集中：公式クライアントが起動しました。ログイン後、各画面を読み込んでから「収集終了」を押してください。", "수집 중: 공식 클라이언트가 열렸습니다. 로그인하여 각 화면을 연 뒤 '수집 종료'를 누르세요.");
            Add(d, "采集状态检查失败：", "採集狀態檢查失敗：", "Capture status check failed: ", "収集状態確認エラー：", "수집 상태 확인 실패: ");
            Add(d, "采集结束，但没有生成账号包。请确认登录公服后确实进入了游戏场景。", "採集結束，但沒有生成帳號包。請確認登入公服後確實進入了遊戲場景。", "Capture finished but no account package was produced. Make sure you signed in to the official server and actually entered a game scene.", "収集を終了しましたがアカウントデータが生成されませんでした。公式サーバーでマップに入場済みか確認してください。", "수집이 종료되었으나 계정 패키지가 생성되지 않았습니다. 로그인 후 게임 맵에 진입했는지 확인해 주세요.");
            Add(d, "采集依赖检查通过。", "採集依賴檢查通過。", "Capture dependency checks passed.", "収集依存チェックに合格しました。", "수집 의존성 점검을 통과했습니다.");
            Add(d, "采集启动结果不完整，请检查状态后重试。", "採集啟動結果不完整，請檢查狀態後重試。", "The capture start result is incomplete. Check the status and retry.", "収集開始結果が不完全です。状態を確認して再試行してください。", "수집 시작 결과가 불완전합니다. 상태를 확인한 후 다시 시도하세요.");
            Add(d, "采集提示", "採集提示", "Capture guidance", "収集ガイド", "수집 안내");
            Add(d, "采集状态结果不完整，请重试。", "採集狀態結果不完整，請重試。", "The capture status result is incomplete. Please retry.", "収集状態の結果が不完全です。再試行してください。", "수집 상태 결과가 불완전합니다. 다시 시도해 주세요.");
            Add(d, "采集进度", "採集進度", "Capture progress", "収集進捗", "수집 진행 상황");
            Add(d, "采集项目清单", "採集項目清單", "Capture checklist", "収集チェックリスト", "수집 체크리스트");
            Add(d, "账号 ZIP 导出结果无效，请重试。", "帳號 ZIP 匯出結果無效，請重試。", "The account ZIP export result is invalid. Please retry.", "ZIP エクスポート結果が無効です。", "계정 ZIP 내보내기 결과가 유효하지 않습니다.");
            Add(d, "检测到本机已有 UID ", "偵測到本機已有 UID ", "An existing local save was found for UID ", "既存のローカルセーブを検出：UID ", "이미 존재하는 로컬 저장 감지: UID ");
            Add(d, " 的存档，但当前私服游戏仍在运行；请先退出游戏后再重复导入覆盖。", " 的存檔，但當前私服遊戲仍在運行；請先退出遊戲後再重複匯入覆蓋。", ", but the private game client is still running. Close the game before overwriting the save.", " がありますが、私服ゲームが実行中のため上書きできません。先にゲームを終了してください。", " 저장 데이터가 있으나 게임이 실행 중입니다. 덮어쓰기 전에 게임을 먼저 종료하세요.");
            Add(d, "账号 ZIP 已交付；游戏运行中，未覆盖原有存档。", "帳號 ZIP 已交付；遊戲運行中，未覆蓋原有存檔。", "Account ZIP delivered; existing save was not overwritten while the game is running.", "アカウント ZIP 保存済み（ゲーム実行中のため既存データ未変更）。", "계정 ZIP 저장됨(게임 실행 중이므로 기존 저장 유지).");
            Add(d, "请先退出游戏", "請先退出遊戲", "Close the game first", "先にゲームを終了してください", "먼저 게임을 종료하세요");
            Add(d, " 的存档。\n\n继续覆盖导入会先自动备份当前数据库与旧导入记录，再用本次采集数据更新该 UID（其他 UID 保留", " 的存檔。\n\n繼續覆蓋匯入會先自動備份當前資料庫與舊匯入記錄，再用本次採集資料更新該 UID（其他 UID 保留", ".\n\nContinuing backs up the current database and previous import folder first, then updates this UID with the new capture (other UIDs stay untouched", " の既存セーブがあります。\n続行するとDBと旧記録を自動バックアップした上でこの UID を更新します（他の UID は保持", " 저장 데이터가 있습니다.\n계속하면 기존 DB를 자동 백업한 후 이 UID만 새 데이터로 덮어씁니다(다른 UID 유지");
            Add(d, "，运行中的服务会先停止", "，運行中的服務會先停止", ", and running services will stop first", "、稼働中のサービスは停止します", ", 실행 중인 서비스는 먼저 중지됨");
            Add(d, "）。\n等级 ", "）。\n等級 ", ").\nLevel ", "）。\nレベル ", ").\n레벨 ");
            Add(d, " 个角色\n\n确认覆盖导入？", " 個角色\n\n確認覆蓋匯入？", " characters\n\nOverwrite this UID?", " 名\n\n上書き取り込みしますか？", "명\n\n이 UID를 덮어쓸까요?");
            Add(d, "确认覆盖已有 UID 存档", "確認覆蓋已有 UID 存檔", "Confirm UID overwrite", "既存 UID 上書き確認", "기존 UID 덮어쓰기 확인");
            Add(d, "已覆盖更新数据库中的 UID ", "已覆蓋更新資料庫中的 UID ", "Updated existing save in database for UID ", "既存データを上書き更新しました：UID ", "기존 저장 데이터를 업데이트했습니다: UID ");
            Add(d, "已覆盖更新：UID ", "已覆蓋更新：UID ", "Updated: UID ", "上書き更新：UID ", "업데이트 완료: UID ");
            Add(d, "存档导入失败。", "存檔匯入失敗。", "Failed to import save.", "セーブ取込に失敗しました。", "저장 데이터 가져오기에 실패했습니다.");
            Add(d, "已写入地图收集与区域解锁：{0} 个场景、{1} 条收集、{2} 个区域、{3} 个月亮。", "已寫入地圖收集與區域解鎖：{0} 個場景、{1} 條收集、{2} 個區域、{3} 個月亮。", "Imported map collections and region unlocks: {0} scenes, {1} collectibles, {2} areas, {3} moons.", "マップ収集・エリア解放を取込：{0} シーン / {1} 収集 / {2} エリア / {3} ムーン。", "맵 수집 및 구역 잠금 해제 반영: {0}개 씬, {1}개 수집품, {2}개 구역, {3}개 달.");
            Add(d, "提示：星云树、大地图总探索度百分比与任务等字段已随原始采集包保存在账号备份中，但当前私服服务端版本尚未实现其读取与下发。", "提示：星雲樹、大地圖總探索度百分比與任務等欄位已隨原始採集包保存在帳號備份中，但當前私服服務端版本尚未實現其讀取與下發。", "Note: Bless Tree, overall map progress %, and quests remain archived in the backup, but this Lolo.exe build does not yet persist or serve them.", "注：星雲樹・全体探索度％・クエスト等はZIP内にアーカイブされますが、現行ローカルサーバーではまだ読み込めません。", "참고: 성운 트리, 전체 맵 탐험도 %, 퀘스트 등은 ZIP에 보관되지만 현재 로컬 서버(Lolo.exe)에서는 아직 로드하지 못합니다.");
            Add(d, "已选中", "已選中", "Selected", "選択中", "선택됨");
            Add(d, "抓包启动失败（", "抓包啟動失敗（", "Capture failed to start (", "キャプチャ開始失敗 (", "캡처 시작 실패 (");
            Add(d, "抓包程序提前退出（", "抓包程式提前退出（", "The capture process exited early (", "キャプチャプロセスが早期終了 (", "캡처 프로세스가 조기 종료됨 (");
            Add(d, "抓包程序返回无效就绪信号，公服未启动。", "抓包程式回傳無效就緒信號，公服未啟動。", "The capture process returned an invalid ready signal; the official client was not started.", "無効な準備完了シグナルです。ゲームは起動されませんでした。", "캡처 프로세스가 잘못된 준비 신호를 반환해 게임이 실행되지 않았습니다.");
            Add(d, "等待抓包启动超时，公服未启动。请查看采集日志。", "等待抓包啟動逾時，公服未啟動。請查看採集日誌。", "Timed out waiting for capture; the official client was not started. See the capture log.", "キャプチャ開始待機がタイムアウトしました。ログを確認してください。", "캡처 시작 대기 시간이 초과되었습니다. 수집 로그를 확인하세요.");
            Add(d, "已有采集正在进行，请先结束当前采集。", "已有採集正在進行，請先結束當前採集。", "A capture is already running; finish it first.", "既に別の収集が実行中です。先に終了してください。", "이미 다른 수집이 실행 중입니다. 먼저 종료하세요.");
            Add(d, "请先停止私服服务，再开始公服采集，避免流量混入私服。", "請先停止私服服務，再開始公服採集，避免流量混入私服。", "Stop private services before official capture to avoid mixing traffic.", "私服サービスを先に停止してから公式収集を開始してください。", "트래픽 혼선을 막기 위해 사설 서버를 먼저 중지한 후 공식 수집을 시작하세요.");
            Add(d, "无法开始采集：", "無法開始採集：", "Cannot start capture: ", "収集を開始できません：", "수집을 시작할 수 없습니다: ");
            Add(d, "当前采集仍在进行，请先由用户结束采集。", "當前採集仍在進行，請先由使用者結束採集。", "Capture is still running; finish it first.", "収集が実行中です。先に終了してください。", "수집이 아직 진행 중입니다. 먼저 종료하세요.");
            Add(d, "没有可恢复的采集会话。", "沒有可恢復的採集會話。", "No capture session is available to recover.", "再開できる収集セッションがありません。", "복구할 수 있는 수집 세션이 없습니다.");
            Add(d, "采集会话路径不属于当前启动器。", "採集會話路徑不屬於當前啟動器。", "The capture session path does not belong to this launcher.", "セッションパスがこのランチャーのものではありません。", "수집 세션 경로가 현재 런처에 속하지 않습니다.");
            Add(d, "没有找到完整的已解码产物，原始文件已保留。", "沒有找到完整的已解碼產物，原始檔案已保留。", "No complete decoded output was found; raw files were kept.", "完全な復号済み出力が見つかりません（生データは保持されています）。", "완전한 디코딩 출력이 없습니다. 원본 파일은 유지되었습니다.");
            Add(d, "采集会话已变化，未改写状态。", "採集會話已變化，未改寫狀態。", "The capture session changed; state was not overwritten.", "セッションが変更されたため状態を上書きしませんでした。", "수집 세션이 변경되어 상태를 덮어쓰지 않았습니다.");
            Add(d, "当前没有采集会话或可恢复的完成产物。", "當前沒有採集會話或可恢復的完成產物。", "No capture session or recoverable output is available.", "有効なセッションや再開可能な出力がありません。", "진행 중이거나 복구할 수집 세션이 없습니다.");
            Add(d, "当前没有进行中的采集，也没有可恢复的完成产物。", "當前沒有進行中的採集，也沒有可恢復的完成產物。", "No capture is running and no recoverable output is available.", "実行中の収集や再開可能データがありません。", "진행 중인 수집이나 복구 가능한 데이터가 없습니다.");
            Add(d, "无法确认采集进程身份；停止信号已保留", "無法確認採集行程身分；停止信號已保留", "Could not confirm the capture process identity; the stop signal was kept", "収集プロセスを検証できないため停止フラグを保持しました", "수집 프로세스 신원을 확인할 수 없어 중지 신호를 유지했습니다");
            Add(d, "无法确认采集进程身份，未自动停止或解包；请检查采集日志。", "無法確認採集行程身分，未自動停止或解包；請檢查採集日誌。", "Could not confirm the capture process identity; nothing was stopped or unpacked. Check the capture log.", "収集プロセスを特定できないため自動停止を中止しました。ログを確認してください。", "수집 프로세스를 확인할 수 없어 자동 중지하지 않았습니다. 수집 로그를 확인하세요.");
            Add(d, "解码失败：", "解碼失敗：", "Decode failed: ", "復号失敗：", "디코딩 실패: ");
            Add(d, "解码未生成完整产物；原始抓包及已有输出已保留。", "解碼未生成完整產物；原始抓包及已有輸出已保留。", "Decode did not produce complete output; the raw capture and existing output were kept.", "復号出力が不完全です（元ファイルは保持）。", "디코딩 출력이 완료되지 않았습니다(원본 캡처는 유지).");
            Add(d, "请选择要交付的账号ZIP及保存位置。", "請選擇要交付的帳號ZIP及儲存位置。", "Select the account ZIP and destination to deliver.", "保存するアカウント ZIP と保存先を選択してください。", "저장할 계정 ZIP과 위치를 선택하세요.");
            Add(d, "缺少采集脚本", "缺少採集腳本", "Capture script is missing", "収集スクリプトがありません", "수집 스크립트가 없습니다");
            Add(d, "系统缺少 PktMon", "系統缺少 PktMon", "PktMon is missing", "Windows PktMon が見つかりません", "시스템에 PktMon이 없습니다");
            Add(d, "采集需要管理员权限", "採集需要管理員權限", "Capture requires administrator rights", "収集には管理者権限が必要です", "수집에는 관리자 권한이 필요합니다");

            // Friendly error strings (keep exact substring contract for test_friendly_errors.py)
            Add(d, "需要检查配置", "需要檢查配置", "Needs a configuration check", "設定の確認が必要", "설정 확인 필요");
            Add(d, "项目", "項目", "Item", "項目", "항목");
            Add(d, "项目状态暂不可读：", "項目狀態暫不可讀：", "Item status is unreadable for now: ", "項目の状態をまだ読み取れません：", "항목 상태를 읽을 수 없음: ");
            Add(d, "已保存自己频道的场景备份：", "已保存自己頻道的場景備份：", "Own-channel scene backups saved: ", "自身のチャンネルのシーンを保存：", "본인 채널 씬 백업 저장: ");
            Add(d, " 个，见 my-scenes 目录；他人频道不纳入。", " 個，見 my-scenes 目錄；他人頻道不納入。", " in the my-scenes folder. Other players' channels are excluded.", " 件（my-scenes フォルダ／他人のチャンネルは除外）。", "개 (my-scenes 폴더 / 타인 채널 제외).");
            Add(d, "操作", "操作", "The operation", "操作", "작업");
            Add(d, "无法完成", "無法完成", "Could not complete ", "完了できませんでした：", "완료할 수 없음: ");
            Add(d, "需要管理员权限；请右键以管理员身份运行后重试。", "需要管理員權限；請右鍵以管理員身份運行後重試。", "Administrator rights are required. Right-click the launcher and choose Run as administrator.", "管理者権限が必要です。右クリックから「管理者として実行」してください。", "관리자 권한이 필요합니다. 마우스 오른쪽 버튼을 눌러 관리자 권한으로 다시 실행하세요.");
            Add(d, "已取消管理员授权，未做任何更改。", "已取消管理員授權，未做任何更改。", "Administrator approval was cancelled. Nothing was changed.", "管理者昇格がキャンセルされました。変更はありません。", "관리자 승인이 취소되어 아무것도 변경되지 않았습니다.");
            Add(d, "无法启动所需程序；请确认安全软件未拦截，并以管理员身份重试。", "無法啟動所需程式；請確認安全軟體未攔截，並以管理員身份重試。", "A required program could not start. Check that security software is not blocking it, then retry as administrator.", "必要なプログラムを起動できませんでした。セキュリティソフトを確認し管理者として再試行してください。", "필요한 프로그램을 시작할 수 없습니다. 보안 프로그램 차단을 확인한 후 관리자 권한으로 다시 시도하세요.");
            Add(d, "缺少必要文件，工具包可能不完整；请重新解压完整便携包后重试。", "缺少必要檔案，工具包可能不完整；請重新解壓完整便攜包後重試。", "Required files are missing; the package may be incomplete. Extract the full portable package again.", "必要なファイルが見つかりません。パッケージ全体を再展開してください。", "필수 파일이 누락되었습니다. 전체 패키지를 다시 압축 해제한 후 시도하세요.");
            Add(d, "文件或目录被占用，可能游戏或另一个启动器仍在运行；请关闭后重试。", "檔案或目錄被佔用，可能遊戲或另一個啟動器仍在運行；請關閉後重試。", "A file or folder is in use, probably because the game or another launcher is still running. Close it and retry.", "ファイルまたはフォルダが使用中です。ゲームや他のランチャーを終了して再試行してください。", "파일 또는 폴더가 사용 중입니다. 게임이나 다른 런처가 실행 중이면 종료한 후 다시 시도하세요.");
            Add(d, "网络连接失败；请检查网络后重试。采集与存档不受影响。", "網路連線失敗；請檢查網路後重試。採集與存檔不受影響。", "The network connection failed. Check your network and retry; captures and saves are unaffected.", "ネットワーク接続に失敗しました（収集とセーブデータには影響ありません）。", "네트워크 연결에 실패했습니다(수집 및 저장 데이터에는 영향 없음).");
            Add(d, "操作超时；服务可能仍在启动。请稍后查看状态再重试。", "操作逾時；服務可能仍在啟動。請稍後查看狀態再重試。", "The operation timed out. The service may still be starting; check the status shortly and retry.", "操作がタイムアウトしました。少し待ってから状態を確認してください。", "작업 시간이 초과되었습니다. 잠시 후 상태를 다시 확인하세요.");
            Add(d, "数据或路径格式不受支持；请确认选择的是正确的文件或目录后重试。", "資料或路徑格式不受支援；請確認選擇的是正確的檔案或目錄後重試。", "That data or path is not supported. Check that you selected the right file or folder and retry.", "サポートされていないデータまたはパス形式です。正しいファイル・フォルダを確認してください。", "지원되지 않는 데이터 또는 경로 형식입니다. 올바른 파일이나 폴더인지 확인하세요.");
            Add(d, "操作已取消；已启动的服务会保留，请查看状态后重试。", "操作已取消；已啟動的服務會保留，請查看狀態後重試。", "Cancelled. Services already started stay running; check the status and retry.", "操作はキャンセルされました（起動済みのサービスは保持されます）。", "작업이 취소되었습니다(이미 시작된 서비스는 유지됨).");
            Add(d, "发生未预期错误，详细原因已写入运行记录；可点「打开日志文件夹」查看。", "發生未預期錯誤，詳細原因已寫入運行記錄；可點「打開日誌資料夾」查看。", "Something unexpected failed. Details were written to the run log; use Open log folder to read them.", "予期しないエラーが発生しました。詳細は実行ログをご確認ください。", "예기치 않은 오류가 발생했습니다. 자세한 내용은 실행 로그 폴더에서 확인하세요.");
            Add(d, "详细原因：", "詳細原因：", "Details: ", "詳細：", "상세 사유: ");
            Add(d, "常见原因与处理建议：", "常見原因與處理建議：", "Common causes and fixes:", "よくある原因と対処法：", "자주 발생하는 원인 및 해결 방법:");
            Add(d, "是否以管理员身份运行？采集抓包需要管理员权限。", "是否以管理員身份運行？採集抓包需要管理員權限。", "Is it running as administrator? Packet capture requires it.", "管理者として実行していますか？（パケット収集には管理者権限が必要です）", "관리자 권한으로 실행하셨나요? (패킷 캡처에는 관리자 권한이 필요합니다)");
            Add(d, "公服客户端是否已经登录并进入场景？", "公服用戶端是否已經登入並進入場景？", "Is the official client logged in and inside a scene?", "公式クライアントにログインしてマップに入りましたか？", "공식 클라이언트에 로그인하여 맵에 진입했나요?");
            Add(d, "是否选择了原版公服 EXE，而不是私服副本？", "是否選擇了原版公服 EXE，而不是私服副本？", "Did you select the original official EXE rather than a private-server copy?", "私服コピーではなく元の公式クライアントを選択していますか？", "사설 사본이 아닌 원본 공식 클라이언트를 선택했나요?");
            Add(d, "是否开启了会拦截抓包的加速器、代理或安全软件？", "是否開啟了會攔截抓包的加速器、代理或安全軟體？", "Is an accelerator, proxy or security tool blocking capture?", "通信を妨げる VPN・プロキシ・セキュリティソフトが動作していませんか？", "캡처를 방해하는 가속기, 프록시 또는 보안 프로그램이 켜져 있나요?");
            Add(d, "是否还有上一次采集没有正常结束？", "是否還有上一次採集沒有正常結束？", "Is an earlier capture still open or not stopped cleanly?", "前回の収集が正常に終了していない可能性はありませんか？", "이전 수집이 정상 종료되지 않은 상태는 아닌가요?");
            Add(d, "服务未响应，可能仍在启动；请稍等并点「刷新」，或用运行记录查看详细原因。", "服務未回應，可能仍在啟動；請稍等並點「重新整理」，或用運行記錄查看詳細原因。", "A service is not responding and may still be starting. Wait and refresh, or check the run log for details.", "サービス応答待ちです。少し待ってから更新するかログを確認してください。", "서비스가 응답하지 않습니다. 잠시 후 새로고침하거나 로그를 확인하세요.");
            Add(d, "游戏文件版本可能已更新；如仍失败请重新选择游戏目录。", "遊戲檔案版本可能已更新；如仍失敗請重新選擇遊戲目錄。", "The game files may have changed. If it keeps failing, pick the game folder again.", "ゲームファイルが更新された可能性があります。直らない場合はフォルダを再選択してください。", "게임 파일 버전이 변경되었을 수 있습니다. 계속 실패하면 폴더를 다시 선택하세요.");
            Add(d, "）。\n导入后外观与装备可能为空，但账号、等级、角色会写入。\n\n仍要导入吗？", "）。\n匯入後外觀與裝備可能為空，但帳號、等級、角色會寫入。\n\n仍要匯入嗎？", ").\nAppearance and equipment may be empty after importing, but the account, level and characters are written.\n\nImport anyway?", "）。\n外見・装備は空になる場合がありますが、基本情報とキャラは取り込まれます。\n\n続行しますか？", ").\n외형 및 장비는 비어 있을 수 있으나 계정, 레벨, 캐릭터는 기록됩니다.\n\n계속 가져올까요?");
            Add(d, "停止请求已返回，但状态检查仍显示服务正在运行。", "停止請求已回傳，但狀態檢查仍顯示服務正在運行。", "Stop was acknowledged, but the status check still shows running services.", "停止応答後もサービスがまだ稼働しています。", "중지 요청 후에도 서비스가 여전히 실행 중으로 표시됩니다.");
            Add(d, "公服启动器路径", "公服啟動器路徑", "Official launcher path", "公式ランチャーパス", "공식 런처 경로");
            Add(d, "公服采集进行中，请先结束采集，再操作私服服务或切换模式。", "公服採集進行中，請先結束採集，再操作私服服務或切換模式。", "Official capture is running. Finish it before operating private services or switching modes.", "公式データ収集の実行中は私服操作やモード切替を行えません。", "공식 수집이 진행 중입니다. 수집을 먼저 종료한 후 이용해 주세요.");
            Add(d, "只读操作与错误记录", "唯讀操作與錯誤記錄", "Read-only operation and error log", "読み取り専用操作・エラー履歴", "읽기 전용 작업 및 오류 기록");
            Add(d, "启动局域网服务  →", "啟動區網服務  →", "Start LAN service  →", "LAN サービス起動  →", "LAN 서비스 시작  →");
            Add(d, "启动请求已返回，但服务尚未就绪。请查看各服务状态后重试。", "啟動請求已回傳，但服務尚未就緒。請查看各服務狀態後重試。", "Start was acknowledged, but the services are not ready. Check each service status and retry.", "起動応答がありましたがサービスの準備ができていません。状態を確認してください。", "시작 요청은 완료되었으나 서비스가 아직 준비되지 않았습니다. 각 상태를 확인하세요.");
            Add(d, "备份请求已返回，但没有确认备份文件，请重试。", "備份請求已回傳，但沒有確認備份檔案，請重試。", "Backup was acknowledged without a backup file. Please retry.", "バックアップファイルを確認できませんでした。再試行してください。", "백업 파일을 확인할 수 없습니다. 다시 시도해 주세요.");
            Add(d, "导入结果未确认写入目标存档，请查看导入报告。", "匯入結果未確認寫入目標存檔，請查看匯入報告。", "The import result did not confirm a write to the target save. Check the import report.", "ターゲットセーブへの書き込み確認が取れませんでした。", "대상 저장소에 기록되었는지 확인되지 않았습니다.");
            Add(d, "操作后无法确认服务状态，请查看状态后重试。", "操作後無法確認服務狀態，請查看狀態後重試。", "Could not verify service status after the operation. Check the status and retry.", "操作後のサービス状態を確認できませんでした。", "작업 후 서비스 상태를 확인할 수 없습니다. 상태를 확인하세요.");
            Add(d, "操作失败", "操作失敗", "Operation failed", "操作失敗", "작업 실패");
            Add(d, "开始采集失败", "開始採集失敗", "Capture failed to start", "収集開始失敗", "수집 시작 실패");
            Add(d, "请核对配置并可点击右下角「打开日志文件夹」查看详细原因。", "請核對配置並可點擊右下角「打開日誌資料夾」查看詳細原因。", "Check your configuration or click Open log folder in the lower right for details.", "設定を確認し、必要に応じて「ログを開く」から詳細をご確認ください。", "설정을 확인하고 오른쪽 아래 '로그 폴더 열기'에서 자세한 내용을 확인하세요.");
            Add(d, "操作已取消", "操作已取消", "Operation cancelled", "キャンセルしました", "작업 취소됨");
            Add(d, "操作结果与当前模式不一致，请重新检查状态。", "操作結果與當前模式不一致，請重新檢查狀態。", "The operation result does not match the current mode. Check the status again.", "操作結果が現在のモードと一致しません。状態を再確認してください。", "작업 결과가 현재 모드와 일치하지 않습니다. 상태를 다시 확인하세요.");
            Add(d, "服务已就绪，但未检测到游戏客户端。请查看客户端日志后重试。", "服務已就緒，但未偵測到遊戲用戶端。請查看用戶端日誌後重試。", "Services are ready, but the game client was not detected. Check the client log and retry.", "サービスは準備完了しましたがゲームクライアントの起動を確認できません。", "서비스는 준비되었으나 게임 클라이언트를 찾지 못했습니다. 로그를 확인하세요.");
            Add(d, "服务控制程序未返回有效结果，请查看运行记录。", "服務控制程式未回傳有效結果，請查看運行記錄。", "The service controller returned an invalid result. See the run log.", "サービスコントローラーの応答が不正です。", "서비스 제어 프로그램이 유효하지 않은 결과를 반환했습니다.");
            Add(d, "服务状态与当前模式不一致，已取消切换。", "服務狀態與當前模式不一致，已取消切換。", "Service status does not match the current mode; switching was cancelled.", "現在のモードとサービス状態が一致しないため切り替えを中止しました。", "서비스 상태가 현재 모드와 일치하지 않아 전환을 취소했습니다.");
            Add(d, "服务状态与当前模式不一致，已取消导入。", "服務狀態與當前模式不一致，已取消匯入。", "Service status does not match the current mode; import was cancelled.", "現在のモードとサービス状態が一致しないため取り込みを中止しました。", "서비스 상태가 현재 모드와 일치하지 않아 가져오기를 취소했습니다.");
            Add(d, "未知服务操作：", "未知服務操作：", "Unknown service operation: ", "不明なサービス操作：", "알 수 없는 서비스 작업: ");
            Add(d, "正在准备导入", "正在準備匯入", "Preparing import", "インポート準備中", "가져오기 준비 중");
            Add(d, "正在准备采集", "正在準備採集", "Preparing capture", "収集準備中", "수집 준비 중");
            Add(d, "正在取消…", "正在取消…", "Cancelling…", "キャンセル中…", "취소 중…");
            Add(d, "正在检查游戏目录", "正在檢查遊戲目錄", "Checking the game folder", "ゲームフォルダ確認中", "게임 폴더 확인 중");
            Add(d, "游戏目录", "遊戲目錄", "Game folder", "ゲームフォルダ", "게임 폴더");
            Add(d, "独立数据目录", "獨立資料目錄", "Isolated data folder", "専用データフォルダ", "독립 데이터 폴더");
            Add(d, "选择运行模式，查看服务状态并管理本地游戏与存档。", "選擇運行模式，查看服務狀態並管理本地遊戲與存檔。", "Choose a run mode, inspect service status, and manage the local game and saves.", "モードを選択してローカルサーバーやセーブデータを管理します。", "실행 모드를 선택하여 로컬 서버와 저장 데이터를 관리합니다.");
            Add(d, "采集启动结果不完整，请检查状态后重试。", "採集啟動結果不完整，請檢查狀態後重試。", "The capture start result is incomplete. Check the status and retry.", "収集開始の結果が不完全です。", "수집 시작 결과가 불완전합니다.");
            Add(d, "采集提示", "採集提示", "Capture guidance", "収集ガイド", "수집 안내");
            Add(d, "采集状态结果不完整，请重试。", "採集狀態結果不完整，請重試。", "The capture status result is incomplete. Please retry.", "収集状態の結果が不完全です。", "수집 상태 결과가 불완전합니다.");
            Add(d, "采集进度", "採集進度", "Capture progress", "収集進捗", "수집 진행");
            Add(d, "采集项目清单", "採集項目清單", "Capture checklist", "収集チェックリスト", "수집 체크리스트");

            // Friendly backend error translations
            Add(d, "运行组件导入失败（缺少内部模块），请确认程序目录 app\\ 文件完整后重试。", "執行元件匯入失敗（缺少內部模組），請確認程式目錄 app\\ 檔案完整後重試。", "Internal module import failed; check app\\ files and retry.", "内部モジュールの読み込みに失敗しました。app\\ フォルダを確認してください。", "내부 모듈을 불러오지 못했습니다. app\\ 폴더를 확인하세요.");
            Add(d, "缺少服务端 LoloServer\\Lolo.exe：请选择包含 LoloServer 的目录，或把服务端放到启动器旁的 server-kit 文件夹。", "缺少服務端 LoloServer\\Lolo.exe：請選擇包含 LoloServer 的目錄，或把服務端放到啟動器旁的 server-kit 資料夾。", "LoloServer\\Lolo.exe is missing; choose a folder with LoloServer or place it in server-kit.", "サーバー LoloServer\\Lolo.exe が見つかりません。フォルダを確認してください。", "LoloServer\\Lolo.exe가 없습니다. 서버 폴더 또는 server-kit를 확인하세요.");
            Add(d, "局域网服务需要本机私网 IPv4 地址（如 192.168.x.x），请检查「服务地址」设置。", "區域網路服務需要本機私網 IPv4 位址（如 192.168.x.x），請檢查「服務位址」設定。", "A private LAN IPv4 address is required.", "プライベート LAN IPv4 アドレス (192.168.x.x など) が必要です。", "사설 LAN IPv4 주소(예: 192.168.x.x)가 필요합니다.");
            Add(d, "当前游戏客户端渠道尚未适配，请使用已验证的 PCGameSDK 或 QuickSDK 客户端版本。", "當前遊戲用戶端渠道尚未適配，請使用已驗證的 PCGameSDK、TapTap QuickSDK 或 Steam 用戶端版本。", "Unsupported or unverified game channel; use verified Bilibili, TapTap QuickSDK, or Steam.", "未検証のクライアントです。検証済みの Bilibili / QuickSDK / Steam 版をご使用ください。", "지원되지 않는 클라이언트 버전입니다. 검증된 Bilibili / QuickSDK / Steam 버전을 사용해 주세요.");
            Add(d, "未找到游戏客户端（launcher.exe / OverField Game），请点击「更改目录」重新选择客户端文件夹。", "未找到遊戲用戶端（launcher.exe / OverField Game），請點擊「變更目錄」重新選擇用戶端資料夾。", "Game client not found (launcher.exe / OverField Game); browse to select the folder.", "クライアントが見つかりません。「変更...」からゲームフォルダを選択してください。", "게임 클라이언트를 찾을 수 없습니다. '변경...'으로 폴더를 다시 선택하세요.");
            Add(d, "所选游戏客户端文件不完整（缺少核心文件），请检查游戏目录或重新校验游戏文件。", "所選遊戲用戶端檔案不完整（缺少核心檔案），請檢查遊戲目錄或重新校驗遊戲檔案。", "Selected game client is incomplete; verify game files.", "選択されたゲームクライアントは不完全です。ファイルを確認してください。", "선택한 게임 클라이언트가 불완전합니다. 게임 파일 무결성을 확인하세요.");
            Add(d, "目录安全性校验未通过（包含软链接或异常目录），请选择真实的本地游戏安装目录。", "目錄安全性校驗未通過（包含軟連結或異常目錄），請選擇真實的本機遊戲安裝目錄。", "Reparse-point/symlink safety check failed; choose the real local installation folder.", "シンボリックリンク/ジャンクション検証に失敗しました。実体のゲームフォルダを選択してください。", "심볼릭 링크/정션 보안 검사에 실패했습니다. 실제 로컬 게임 폴더를 선택하세요.");
            Add(d, "配置客户端版本映射失败：游戏文件可能不完整或 ClientVersion.json 异常。", "配置用戶端版本映射失敗：遊戲檔案可能不完整或 ClientVersion.json 異常。", "Client version mapping failed; game files or ClientVersion.json may be incomplete.", "クライアントのバージョン構成の適用に失敗しました。", "클라이언트 버전 매핑 설정에 실패했습니다.");
            Add(d, "准备运行配置失败（请核对游戏目录、服务端文件或局域网地址）；详细原因见运行记录。", "準備運行配置失敗（請核對遊戲目錄、服務端檔案或區域網路位址）；詳細原因見運行記錄。", "Failed to prepare profile; check game directory, server files, or LAN address.", "プロファイル準備に失敗しました。パス・サーバー・アドレス設定をご確認ください。", "프로필 준비에 실패했습니다. 게임 폴더, 서버 파일 또는 LAN 주소를 확인하세요.");
            Add(d, "本地服务端或网关启动后意外退出，请点击「打开日志文件夹」查看 server/rooms 错误日志。", "本地服務端或閘道啟動後意外退出，請點擊「打開日誌資料夾」查看 server/rooms 錯誤日誌。", "Local server or gateway exited unexpectedly; check logs.", "ローカルサーバーまたはゲートウェイが異常終了しました。ログを確認してください。", "로컬 서버 또는 게이트웨이가 예기치 않게 종료되었습니다. 로그 폴더를 확인하세요.");
            Add(d, "无法启动本地服务进程；请确认安全软件未拦截且 server-kit / python 文件完整。", "無法啟動本地服務進程；請確認安全軟體未攔截且 server-kit / python 檔案完整。", "Could not start local service process; check antivirus and python/server files.", "サービスプロセスを起動できません。セキュリティソフトとファイル構成を確認してください。", "로컬 서비스 프로세스를 시작할 수 없습니다. 보안 프로그램 및 파일 구성을 확인하세요.");
            Add(d, "所需本地端口已被占用，请先点击「停止服务」或关闭冲突程序后重试。", "所需本地埠號已被佔用，請先點擊「停止服務」或關閉衝突程式後重試。", "Required port is already in use; stop services or close conflicting apps.", "ポートが既に使用されています。サービス停止または競合アプリ終了後に再試行してください。", "필요한 포트가 이미 사용 중입니다. 서비스를 중지하거나 충돌 앱을 종료하세요.");
            Add(d, "本地服务端口未在规定时间内就绪或已被其他进程占用，请先停止服务或检查端口占用后重试。", "本地服務埠號未在規定時間內就緒或已被其他進程佔用，請先停止服務或檢查埠號佔用後重試。", "Local service port timed out or is in use.", "ローカルポートが時間内に準備できませんでした。", "로컬 서비스 포트가 제시간에 준비되지 않았습니다.");
            Add(d, "本地游戏客户端未能启动，请点击「打开日志文件夹」查看 client-stderr.log。", "本地遊戲用戶端未能啟動，請點擊「打開日誌資料夾」查看 client-stderr.log。", "Local game client failed to launch; check client-stderr.log.", "ゲームクライアントの起動に失敗しました。client-stderr.log を確認してください。", "로컬 게임 클라이언트를 시작하지 못했습니다. client-stderr.log를 확인하세요.");
            Add(d, "准备本地 UID 账号失败，请确认存档数据库未被占用后重试。", "準備本地 UID 帳號失敗，請確認存檔資料庫未被佔用後重試。", "Failed to prepare local UID account; make sure database is not locked.", "ローカル UID アカウントの準備に失敗しました。DB がロックされていないか確認してください。", "로컬 UID 계정 준비에 실패했습니다. DB가 사용 중이 아닌지 확인하세요.");
            Add(d, "请先退出正在运行的游戏客户端，再切换运行模式或修改 UID。", "請先退出正在運行的遊戲用戶端，再切換運行模式或修改 UID。", "Close the game before changing mode or UID.", "モードや UID を変更する前にゲームを終了してください。", "모드나 UID를 변경하기 전에 먼저 게임을 종료하세요.");
            Add(d, "请先点击「停止服务」，再切换运行模式或局域网地址。", "請先點擊「停止服務」，再切換運行模式或區網位址。", "Stop the current world before switching modes or addresses.", "切り替え前に「サービス停止」を押してください。", "전환하기 전에 '서비스 중지'를 먼저 눌러 주세요.");
            Add(d, "上一次服务操作仍在执行中，请稍候再试。", "上一次服務操作仍在執行中，請稍候再試。", "Another world operation is running; wait a moment.", "前の操作がまだ進行中です。少しお待ちください。", "이전 작업이 아직 실행 중입니다. 잠시 후 다시 시도하세요.");
            Add(d, "检测到旧版启动器留下的运行中服务，请先用原启动器停止服务后再试。", "偵測到舊版啟動器留下的運行中服務，請先用原啟動器停止服務後再試。", "Legacy launcher services are still running; stop them first.", "旧ランチャーのサービスが稼働中です。先に停止してください。", "이전 버전 런처의 서비스가 실행 중입니다. 먼저 중지해 주세요.");
            Add(d, "当前渠道客户端需要 Frida 17.24.3 运行依赖。", "當前區服（TapTap / Steam）用戶端需要 Frida 17.24.3 運行依賴（見 runtime-python）。", "This channel client requires Frida 17.24.3.", "このサーバー（TapTap / Steam）の起動には Frida 17.24.3 が必要です。", "이 서버(TapTap / Steam) 실행에는 Frida 17.24.3 의존성이 필요합니다.");
            Add(d, "账号 ZIP 包含客户端请求记录（c2s），安全策略已拦截保存与导入；请使用当前版本重新结束采集或重新解包。", "帳號 ZIP 包含用戶端請求記錄（c2s），安全策略已攔截儲存與匯入。", "Client requests (c2s) are not allowed in an account ZIP.", "クライアント送信パケット(c2s)が含まれているため拒否されました。", "클라이언트 요청 기록(c2s)이 포함되어 저장/가져오기가 차단되었습니다.");
            Add(d, "原始网络抓包文件（.etl / .pcapng）不能直接导入或保存为账号 ZIP。", "原始網路封包檔（.etl / .pcapng）不能直接匯入或儲存為帳號 ZIP。", "Raw capture files (.etl / .pcapng) cannot be imported or saved as account ZIPs.", "生のキャプチャファイル(.etl / .pcapng)は直接インポートできません。", "원본 패킷 캡처 파일(.etl / .pcapng)은 직접 가져올 수 없습니다.");
            Add(d, "生成的账号 ZIP 中缺少有效 UID，请确认已登录公服并进入游戏场景。", "生成的帳號 ZIP 中缺少有效 UID，請確認已登入公服並進入遊戲場景。", "Account ZIP has no verifiable UID.", "アカウント ZIP に有効な UID が含まれていません。", "생성된 계정 ZIP에 유효한 UID가 없습니다.");
            Add(d, "检测到账号包内含敏感凭据字段，安全校验已拦截保存与导入。", "偵測到帳號包內含敏感憑證欄位，安全校驗已攔截儲存與匯入。", "Account ZIP contains sensitive credentials and was blocked.", "機密情報が含まれているため安全チェックで拒否されました。", "민감한 인증 정보가 감지되어 보안 검사에 의해 차단되었습니다.");
            Add(d, "当前模式数据目录尚未初始化，请先准备配置（或启动一次服务）后再导入。", "當前模式資料目錄尚未初始化，請先準備配置（或啟動一次服務）後再匯入。", "Profile database is not initialized yet; start or prepare once first.", "このモードのデータが未初期化です。一度起動してから取り込んでください。", "현재 모드 데이터 디렉터리가 초기화되지 않았습니다. 먼저 한 번 실행한 뒤 가져오세요.");
            Add(d, "抓包角色或物品字段存在冲突，未能写入存档；请查看运行记录中的详细字段。", "抓包角色或物品欄位存在衝突，未能寫入存檔；請查看運行記錄中的詳細欄位。", "Capture contains conflicting character or item fields.", "取得データに矛盾するフィールドがあるため中止しました。", "수집 데이터에 충돌하는 필드가 있어 기록하지 못했습니다.");
            Add(d, "更新检查访问接口受限，请稍后重试；原版本、采集与存档不受影响。", "更新檢查存取介面受限，請稍後重試；原版本、採集與存檔不受影響。", "Update rate limited; try again later.", "更新チェックのレート制限に達しました。後で再試行してください。", "업데이트 확인 요청 제한에 걸렸습니다. 나중에 다시 시도하세요.");
            Add(d, "更新服务器连接失败，请检查网络或稍后重试；原版本可继续使用。", "更新伺服器連線失敗，請檢查網路或稍後重試；原版本可繼續使用。", "Could not reach update server; current version remains usable.", "更新サーバーに接続できません。現行バージョンはそのまま利用できます。", "업데이트 서버에 연결하지 못했습니다. 현재 버전은 계속 사용할 수 있습니다.");

            // Updater strings
            Add(d, "自动更新 · 就绪", "自動更新 · 就緒", "Auto update · Ready", "自動更新 · 準備完了", "자동 업데이트 · 준비됨");
            Add(d, "仅更新启动器；游戏与存档不变", "僅更新啟動器；遊戲與存檔不變", "Launcher only; games and saves unchanged", "ランチャー本体のみ更新（ゲーム・セーブ変更なし）", "런처만 업데이트 (게임 및 저장 데이터 유지)");
            Add(d, "检查更新", "檢查更新", "Check now", "更新確認", "업데이트 확인");
            Add(d, "暂停自动", "暫停自動", "Pause auto", "自動停止", "자동 중지");
            Add(d, "恢复自动", "恢復自動", "Resume auto", "自動再開", "자동 재개");
            Add(d, "稍后更新", "稍後更新", "Later", "後で", "나중에");
            Add(d, "恢复更新", "恢復更新", "Recover update", "更新再開", "업데이트 복구");
            Add(d, "已请求恢复更新 · 等待空闲（稍后设置仍生效）", "已請求恢復更新 · 等待閒置（稍後設定仍生效）", "Recovery requested · Waiting for idle (Later remains respected)", "更新復旧を予約 · アイドル待機中", "업데이트 복구 요청됨 · 유휴 대기 중");
            Add(d, "自动更新已恢复", "自動更新已恢復", "Automatic updates resumed", "自動更新を再開しました", "자동 업데이트가 재개되었습니다");
            Add(d, "自动更新已暂停；已开始的下载可完成", "自動更新已暫停；已開始的下載可完成", "Paused; an active download may finish", "自動更新を一時停止しました", "자동 업데이트가 일시 중지되었습니다");
            Add(d, "已推迟30分钟；下载保留", "已推遲30分鐘；下載保留", "Deferred 30 minutes; download retained", "30分延期しました（ダウンロード保持）", "30분 연기됨 (다운로드 유지)");
            Add(d, "开发 / 测试模式 · 更新已禁用", "開發 / 測試模式 · 更新已停用", "Development / test mode · Updates disabled", "開発/テストモード · 更新無効", "개발/테스트 모드 · 업데이트 비활성화");
            Add(d, "读取更新设置", "讀取更新設定", "Reading update settings", "更新設定の読み込み", "업데이트 설정 읽기");
            Add(d, "保存更新设置", "儲存更新設定", "Saving update settings", "更新設定の保存", "업데이트 설정 저장");
            Add(d, "更新设置读取失败，自动更新已暂停：", "更新設定讀取失敗，自動更新已暫停：", "Update settings unavailable; automatic updates paused: ", "更新設定の読み込みに失敗しました：", "업데이트 설정을 읽을 수 없어 자동 업데이트가 중지됨: ");
            Add(d, "自动更新 · 等待后台检查", "自動更新 · 等待背景檢查", "Auto update · Waiting for background check", "自動更新 · バックグラウンド確認待ち", "자동 업데이트 · 백그라운드 확인 대기");
            Add(d, "自动更新已暂停", "自動更新已暫停", "Automatic updates paused", "自動更新一時停止中", "자동 업데이트 일시 중지됨");
            Add(d, "更新需要恢复 · 等待游戏 / 服务 / 采集空闲", "更新需要恢復 · 等待遊戲 / 服務 / 採集閒置", "Update recovery needed · Waiting for game / services / capture", "更新復旧待機 · ゲーム・サービス終了待ち", "업데이트 복구 대기 · 게임/서비스 종료 대기");
            Add(d, "更新已下载 · 等待游戏 / 服务 / 采集空闲", "更新已下載 · 等待遊戲 / 服務 / 採集閒置", "Downloaded · Waiting for game / services / capture to finish", "ダウンロード済 · ゲーム・サービス終了待ち", "다운로드됨 · 게임/서비스 종료 대기");
            Add(d, "恢复更新 · 空闲重启倒计时 ", "恢復更新 · 閒置重啟倒數 ", "Recover update · Restart while idle in ", "更新復旧 · 自動再起動まで ", "업데이트 복구 · 재시작 카운트다운 ");
            Add(d, "更新已就绪 · 空闲重启倒计时 ", "更新已就緒 · 閒置重啟倒數 ", "Update ready · Restart while idle in ", "更新準備完了 · 自動再起動まで ", "업데이트 준비 · 재시작 카운트다운 ");
            Add(d, " 秒（可稍后）", " 秒（可稍後）", "s (Later to defer)", " 秒（延期可能）", "초 (나중에 가능)");
            Add(d, "正在后台检查并下载正式版…", "正在背景檢查並下載正式版…", "Checking / downloading stable release in background…", "正式版を確認・ダウンロード中…", "정식 버전 확인 및 다운로드 중…");
            Add(d, "正式版已下载 · 等待空闲安全重启", "正式版已下載 · 等待閒置安全重啟", "Stable release downloaded · Waiting for a safe idle restart", "正式版ダウンロード完了 · アイドル再起動待ち", "정식 버전 다운로드 완료 · 안전한 재시작 대기 중");
            Add(d, "已是最新正式版", "已是最新正式版", "Latest stable release installed", "最新の正式版です", "최신 정식 버전이 설치되어 있습니다");
            Add(d, "更新检查已推迟 · 稍后可重试", "更新檢查已推遲 · 稍後可重試", "Update deferred · Retry later", "更新チェックを延期しました", "업데이트 확인이 연기되었습니다");
            Add(d, "检测到未完成更新 · 等待空闲恢复，可手动恢复或稍后", "偵測到未完成更新 · 等待閒置恢復，可手動恢復或稍後", "Unfinished update detected · Recover when idle, manually, or Later", "未完了の更新を検出 · アイドル時または手動で復旧可能", "미완료 업데이트 감지 · 유휴 상태 또는 수동으로 복구 가능");
            Add(d, "更新需要恢复 · 等待空闲或状态确认", "更新需要恢復 · 等待閒置或狀態確認", "Recovery needed · Waiting for idle / confirmed status", "復旧待ち · 状態確認中", "복구 필요 · 상태 확인 대기 중");
            Add(d, "更新已下载 · 等待空闲或状态确认", "更新已下載 · 等待閒置或狀態確認", "Downloaded · Waiting for idle / confirmed status", "ダウンロード済 · 状態確認待ち", "다운로드됨 · 상태 확인 대기 중");
            Add(d, "更新暂不可用 · 原版本可继续使用", "更新暫不可用 · 原版本可繼續使用", "Update unavailable · Current version remains usable", "更新を一時利用できません（現バージョン利用可）", "업데이트 일시 불가 (기존 버전 사용 가능)");
            Add(d, "自动更新：", "自動更新：", "Auto update: ", "自動更新：", "자동 업데이트: ");
            Add(d, "自动更新", "自動更新", "Auto update", "自動更新", "자동 업데이트");
            Add(d, "更新设置损坏或不可读 · 已暂停自动更新", "更新設定損壞或不可讀 · 已暫停自動更新", "Update settings invalid / unreadable · Auto updates paused", "更新設定が破損しているため自動更新を停止しました", "업데이트 설정 파일 손상 · 자동 업데이트 일시 중지됨");
            Add(d, "更新设置保存失败：", "更新設定儲存失敗：", "Cannot save update settings: ", "更新設定の保存失敗：", "업데이트 설정 저장 실패: ");
            Add(d, "已更新到 ", "已更新到 ", "Updated to ", "更新完了：", "업데이트 완료: ");
            Add(d, " · 当前已是最新", " · 目前已是最新", " · Up to date", " · 最新", " · 최신 버전");
            Add(d, "已下载 ", "已下載 ", "Downloaded ", "ダウンロード済：", "다운로드 완료: ");
            Add(d, " · 空闲后安装", " · 閒置後安裝", " · installs when idle", " · アイドル時に適用", " · 유휴 시 설치");
            Add(d, "检测到本地受管文件已修改 · 自动更新已暂停并保留修改", "偵測到本機受管檔案已修改 · 自動更新已暫停並保留修改", "Local managed files edited · Auto-update paused (edits kept)", "ローカル変更を検出 · 自動更新を一時停止し変更を保持します", "로컬 파일 변경 감지 · 자동 업데이트 중지 및 변경 유지");
            Add(d, "自动更新：检测到本地受管文件已修改，已暂停并保留修改：", "自動更新：偵測到本機受管檔案已修改，已暫停並保留修改：", "Auto update: local managed files edited; paused and preserved edits: ", "自動更新：ローカル変更を検出したため一時停止し変更を保持しました：", "자동 업데이트: 로컬 파일 변경으로 자동 업데이트 중지됨: ");
            Add(d, "更新基线缺失或版本不兼容 · 自动更新已暂停", "更新基線缺失或版本不相容 · 自動更新已暫停", "Installation baseline missing or incompatible · Auto-update paused", "更新ベースライン不整合 · 自動更新を一時停止しました", "설치 기준 불일치 · 자동 업데이트 중지됨");
            Add(d, "自动更新：当前安装基线与更新包不匹配，已暂停自动更新（原版本可继续使用）。", "自動更新：目前安裝基線與更新包不匹配，已暫停自動更新（原版本可繼續使用）。", "Auto update: installation baseline does not match update package; automatic updates paused.", "自動更新：現在のインストールベースラインと不一致のため自動更新を停止しました。", "자동 업데이트: 현재 설치 기준과 일치하지 않아 자동 업데이트를 중지했습니다.");

            // Validation & misc errors
            Add(d, "请选择原版公服启动器，不要选择私服启动器或服务端。", "請選擇原版公服啟動器，不要選擇私服啟動器或服務端。", "Choose the untouched official client, not a private launcher or server.", "私服版ではなく、公式のオリジナルクライアントを選択してください。", "사설 런처가 아닌 원본 공식 클라이언트를 선택해 주세요.");
            Add(d, "该目录是已准备的私服客户端副本，不能用于公服采集。", "該目錄是已準備的私服客戶端副本，不能用於公服採集。", "This folder is a prepared private-server copy and cannot be used for official capture.", "これは私服用の複製ディレクトリのため、公式収集には使えません。", "준비된 사설 클라이언트 사본 폴더이므로 공식 수집에 사용할 수 없습니다.");
            Add(d, "请选择已存在的公服启动器、原版客户端 EXE，或 Steam 游戏快捷方式。", "請選擇已存在的公服啟動器、原版用戶端 EXE，或 Steam 遊戲捷徑。", "Choose an existing official launcher EXE or Steam .url shortcut.", "既存の公式クライアント EXE または Steam .url を選択してください。", "존재하는 공식 클라이언트 EXE 또는 Steam .url 바로가기를 선택하세요.");
            Add(d, "公服路径不能指向启动器 profiles 内的私服副本。", "公服路徑不能指向啟動器 profiles 內的私服副本。", "The official path cannot point inside the launcher's private profiles.", "公式パスに profiles 配下の私服ディレクトリは指定できません。", "공식 경로가 런처 profiles 내부의 사설 사본을 가리킬 수 없습니다.");
            Add(d, "这个客户端已经替换本地UID SDK，请选择未修改的公服客户端。", "這個用戶端已經替換本地UID SDK，請選擇未修改的公服用戶端。", "This client uses the replaced LocalUidSDK; select an untouched official client.", "LocalUidSDK が適用済みのクライアントです。未改変の公式クライアントを選んでください。", "로컬 UID SDK가 적용된 클라이언트입니다. 원본 공식 클라이언트를 선택하세요.");
            Add(d, "请先设置公服启动器路径；该路径与私服游戏目录独立。", "請先設定公服啟動器路徑；該路徑與私服遊戲目錄獨立。", "Set the official launcher path first (independent of your private game folder).", "先に公式クライアントのパスを設定してください。", "먼저 공식 클라이언트 경로를 설정하세요.");
            Add(d, "无有效服务端", "無有效服務端", "No valid server", "サーバーが見つかりません", "유효한 서버 없음");
            Add(d, "已根据网络区域初始化语言：", "已根據網路區域初始化語言：", "Language initialized by region: ", "ネットワーク地域に基づき言語を設定しました：", "네트워크 지역에 따라 언어를 초기화했습니다: ");
            Add(d, "一键导入失败。", "一鍵匯入失敗。", "One-click import failed.", "ワンクリックインポートに失敗しました。", "원클릭 가져오기에 실패했습니다.");
            Add(d, "一键导入：选择资源包 / 账号ZIP / JSON / lolo.db（也可直接拖入窗口）", "一鍵匯入：選擇資源包 / 帳號ZIP / JSON / lolo.db（也可直接拖入視窗）", "One-Click Import: Select ZIP / JSON / lolo.db (or drag & drop here)", "ワンクリック取込：ZIP / JSON / lolo.db を選択（D&D 可）", "원클릭 가져오기: ZIP / JSON / lolo.db 선택 (창에 드래그 가능)");
            Add(d, "一键导入支持包 (*.zip;*.json;*.db;*.sqlite)|*.zip;*.json;*.db;*.sqlite|账号备份 / 解码抓包 ZIP|*.zip|所有文件|*.*", "一鍵匯入支援包 (*.zip;*.json;*.db;*.sqlite)|*.zip;*.json;*.db;*.sqlite|帳號備份 / 解碼抓包 ZIP|*.zip|所有檔案|*.*", "Supported Packages (*.zip;*.json;*.db;*.sqlite)|*.zip;*.json;*.db;*.sqlite|Account Backup ZIP|*.zip|All Files|*.*", "対応パッケージ (*.zip;*.json;*.db;*.sqlite)|*.zip;*.json;*.db;*.sqlite|アカウント ZIP|*.zip|すべて|*.*", "지원 패키지 (*.zip;*.json;*.db;*.sqlite)|*.zip;*.json;*.db;*.sqlite|계정 백업 ZIP|*.zip|모든 파일|*.*");
            Add(d, "指定导入 UID", "指定匯入 UID", "Target UID", "取り込み UID 指定", "대상 UID 지정");
            Add(d, "该资源包中未包含玩家 UID（可为家园/部分数据包），请输入目标本地 UID：", "該資源包中未包含玩家 UID（可為家園/部分資料包），請輸入目標本機 UID：", "This package has no UID (for example a garden or partial pack). Enter local target UID:", "このデータには UID が含まれていません。取り込み先のローカル UID を入力してください：", "이 패키지에는 UID가 없습니다(예: 하우징/부분 데이터). 로컬 UID를 입력하세요:");
            Add(d, "角色", "角色", "Characters", "キャラ", "캐릭터");
            Add(d, "背包", "背包", "Bag", "バッグ", "가방");
            Add(d, "武器", "武器", "Weapons", "武器", "무기");
            Add(d, "护甲", "護甲", "Armor", "防具", "방어구");
            Add(d, "海报", "海報", "Posters", "ポスター", "포스터");
            Add(d, "外观", "外觀", "Outfits", "衣装", "외형");
            Add(d, "家园", "家園", "Garden", "マイホーム", "마이홈");
            Add(d, "探索", "探索", "Exploration", "探索", "탐험");
            Add(d, "无", "無", "None", "なし", "없음");
            Add(d, "\n包含模块：", "\n包含模組：", "\nIncluded modules: ", "\n含まれるデータ：", "\n포함된 항목: ");
            Add(d, "\n缺失留空：", "\n缺失留空：", "\nMissing (left empty): ", "\n未収録（空欄扱い）：", "\n누락 항목(빈 값 유지): ");
            Add(d, "\n\n将导入当前模式的独立数据目录；缺失部分自动留空，已有数据会先备份，其他 UID 保留。", "\n\n將匯入當前模式的獨立資料目錄；缺失部分自動留空，已有資料會先備份，其他 UID 保留。", "\n\nImports into this mode's isolated profile; missing sections stay empty, existing saves are backed up first, and other UIDs are kept.", "\n\n現在のモードへ取り込みます。不足データは空欄のまま、既存データは事前にバックアップされます。", "\n\n현재 모드의 데이터 폴더로 가져옵니다. 없는 항목은 비워 두며 기존 데이터는 백업됩니다.");
            Add(d, "\n注意：本机已存在该 UID 的存档，继续将备份旧档后覆盖。", "\n注意：本機已存在該 UID 的存檔，繼續將備份舊檔後覆蓋。", "\nNote: this UID already exists locally and will be backed up then overwritten.", "\n注意：この UID は既存のためバックアップ後に上書きされます。", "\n주의: 이 UID가 이미 존재하므로 백업 후 덮어씁니다.");
            Add(d, "已导入 UID ", "已匯入 UID ", "Imported UID ", "UID を取り込みました：", "UID 가져오기 완료: ");
            Add(d, " 的资料。原数据已有备份，其他 UID 保留。", " 的資料。原資料已有備份，其他 UID 保留。", " data. Previous data is backed up and other UIDs remain intact.", " のデータ（既存はバックアップ済み・他 UID 保持）。", " 데이터 (기존 백업 완료, 다른 UID 유지).");
            Add(d, "确定", "確定", "OK", "OK", "확인");
            Add(d, "取消", "取消", "Cancel", "キャンセル", "취소");
            Add(d, " 个角色", " 個角色", " characters", " 名", "명");
            Add(d, "正在停止私服服务并准备采集", "正在停止私服服務並準備採集", "Stopping private server before capture", "私服サーバーを停止して収集準備中", "사설 서버 중지 및 수집 준비 중");
            Add(d, "已自动停止运行中的私服服务。", "已自動停止運行中的私服服務。", "Stopped idle private server services.", "待機中の私服サービスを自動停止しました。", "실행 중이던 사설 서버를 자동 중지했습니다.");
            Add(d, "请先退出私服游戏，再开始公服采集，避免流量混入私服。", "請先退出私服遊戲，再開始公服採集，避免流量混入私服。", "Close the private game client before starting official capture to avoid mixing traffic.", "私服クライアントを終了してから公式収集を開始してください。", "트래픽 혼선을 막기 위해 사설 게임 클라이언트를 종료한 후 공식 수집을 시작하세요.");

            return d;
        }

        static string FieldFor(LocaleEntry entry, string locale)
        {
            if (locale == "zh-TW") return entry.Tw;
            if (locale == "ja") return entry.Ja;
            if (locale == "ko") return entry.Ko;
            return entry.En;
        }

        static string Lookup(string locale, string text)
        {
            LocaleEntry entry;
            if (Entries.TryGetValue(text, out entry)) return FieldFor(entry, locale);

            string bestKey = null, bestValue = null;
            foreach (var pair in Entries)
            {
                bool isPrefix = pair.Key.Length >= 8
                    || pair.Key.EndsWith("：", StringComparison.Ordinal)
                    || pair.Key.EndsWith("（", StringComparison.Ordinal)
                    || pair.Key.EndsWith("；", StringComparison.Ordinal);
                if (isPrefix && text.StartsWith(pair.Key, StringComparison.Ordinal)
                    && (bestKey == null || pair.Key.Length > bestKey.Length))
                {
                    bestKey = pair.Key;
                    bestValue = FieldFor(pair.Value, locale);
                }
            }
            return bestKey == null ? null : bestValue + text.Substring(bestKey.Length);
        }

        public static string S(string text)
        {
            if (text == null || CurrentLocale == "zh-CN") return text;
            string translated = Lookup(CurrentLocale, text);
            return translated ?? text;
        }

        public static string T(string text, string englishFallback)
        {
            if (text == null || CurrentLocale == "zh-CN") return text;
            if (CurrentLocale == "en" && !String.IsNullOrEmpty(englishFallback)) return englishFallback;
            string translated = Lookup(CurrentLocale, text);
            return translated ?? (String.IsNullOrEmpty(englishFallback) ? text : englishFallback);
        }
    }
}
