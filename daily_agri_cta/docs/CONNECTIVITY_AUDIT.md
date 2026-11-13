# M0 官方源连通性审计

实际执行日期：2026-11-13 UTC。以下为本环境实际响应；页面成功不等于结构化历史采集成功。全部错误响应与原始快照留在受控data_audit目录。

|来源|检查|实际结果|地址|
|---|---|---|---|
|CZCE|page|HTTP 412：412 Client Error: Precondition Failed for url: https://www.czce.com.cn/|https://www.czce.com.cn|
|CZCE|robots|HTTP 404：404 Client Error: Not Found for url: https://www.czce.com.cn/robots.txt|https://www.czce.com.cn/robots.txt|
|DCE|page|HTTP 503：503 Server Error: Service Unavailable for url: https://www.dce.com.cn/|https://www.dce.com.cn|
|DCE|robots|HTTP 503：503 Server Error: Service Unavailable for url: https://www.dce.com.cn/robots.txt|https://www.dce.com.cn/robots.txt|
|SHFE|page|HTTP 200：page_only_http_200|https://www.shfe.cn/reports/tradedata/dailyandweeklydata/|
|SHFE|robots|HTTP 404：404 Client Error: Not Found for url: https://www.shfe.cn/robots.txt|https://www.shfe.cn/robots.txt|
|INE|page|HTTP 200：page_only_http_200|https://www.ine.cn|
|INE|robots|HTTP 404：404 Client Error: Not Found for url: https://www.ine.cn/robots.txt|https://www.ine.cn/robots.txt|
|MPOB|page|HTTP 200：page_only_http_200|https://bepi.mpob.gov.my/stat/web_report1.php?val=202684|
|MPOB|robots|HTTP 404：404 Client Error: Not Found for url: https://bepi.mpob.gov.my/robots.txt|https://bepi.mpob.gov.my/robots.txt|
|MPOB_HISTORY|page|HTTP 200：page_only_http_200|https://bepi.mpob.gov.my/|
|MPOB_HISTORY|robots|HTTP 404：404 Client Error: Not Found for url: https://bepi.mpob.gov.my/robots.txt|https://bepi.mpob.gov.my/robots.txt|
|NOAA_ENSO|page|HTTP 200：page_only_http_200|https://www.cpc.ncep.noaa.gov/data/indices/ersst5.nino.mth.91-20.ascii|
|NOAA_ENSO|robots|HTTP 404：404 Client Error: Not Found for url: https://www.cpc.ncep.noaa.gov/robots.txt|https://www.cpc.ncep.noaa.gov/robots.txt|
|USDA_NASS|page|HTTP 404：404 Client Error: Not Found for url: https://www.nass.usda.gov/Quick_Stats/|https://www.nass.usda.gov/Quick_Stats/|
|USDA_NASS|robots|HTTP 200：page_only_http_200|https://www.nass.usda.gov/robots.txt|
|USDA_WASDE|page|HTTP 200：page_only_http_200|https://www.usda.gov/oce/commodity/wasde|
|USDA_WASDE|robots|HTTP 200：page_only_http_200|https://www.usda.gov/robots.txt|
|UNICA|page|HTTP 202：unverified_challenge_html|https://unica.com.br|
|UNICA|robots|HTTP 202：unverified_challenge_html|https://unica.com.br/robots.txt|
|NCC|page|HTTP 503：503 Server Error: Service Unavailable for url: https://www.china-cotton.org/|https://www.china-cotton.org/|
|NCC|robots|HTTP 503：503 Server Error: Service Unavailable for url: https://www.china-cotton.org/robots.txt|https://www.china-cotton.org/robots.txt|
|SDIC_NOTICE|page|HTTP 200：page_only_http_200|https://www.sdicfutures.com.cn/annc/business/11098.html|
|SDIC_NOTICE|robots|HTTP 503：503 Server Error: Service Unavailable for url: https://www.sdicfutures.com.cn/robots.txt|https://www.sdicfutures.com.cn/robots.txt|
|SDIC_MARGIN|page|HTTP 200：page_only_http_200|https://www.sdicfutures.com.cn/service/publicity/11247.html|
|SDIC_MARGIN|robots|HTTP 503：503 Server Error: Service Unavailable for url: https://www.sdicfutures.com.cn/robots.txt|https://www.sdicfutures.com.cn/robots.txt|
|VENDOR_IMPORT|授权|HTTP —：requires_authorization|MANUAL_AUTHORIZED_IMPORT|

## 结构化历史核验与下载规则

- MPOB：官网Summary 2026文章iframe指向web_report1.php?val=202684。搜索发现官网web_internal.php?val=202484与202584，并核验标题/月份/表宽及吨数口径；同一官方年表规则YYYY84的202384实际返回2023表，已解析，不是填入推测数值。2023–2025各12月，2026至10月。原始页保留初值/修订标记；无法重建首次采集前所有修订版本。
- MPOB Summary归档目录：https://bepi.mpob.gov.my/index.php/import/967-archive-summary-list （目录内容并不完整覆盖所有近年，不能据此说历史全缺）。年表当前无需登录；官网其他统计存在注册/订阅入口，未绕过。
- NOAA：指定CPC ASCII 200，头字段已校验；取第十列NINO3.4 ANOM，ERSSTv5、1991–2020基期，918个月（1950-01至2026-06）。不是ONI三个月滑动平均，不与其他基期拼接。2026-07后未取得。
- SHFE：官方日周页面引入/images/api.js，公开脚本声明kx{date}.dat、旧仓单dat及新版stockdata/dailystock_{date}/ZH/all.html。日行情2026-11-12成功；旧仓单dat/HTML路径404，新版同日页面成功。2025部分日期新版仓单404，只记录缺口。
- SHFE完整年度归档从https://www.shfe.cn/reports/tradedata/datadownload/download.json实际列出的2025期货URL下载。HTTP200，76,397,695字节，包含12个月SHFE XLSX和1个全年INE XLSX；RU/NR按实际合约与交易日期解析。未使用API推测路径；api.js另列出的MarketData_Year_2025.zip实测404，弃用。
- /data/calendar/calendar_2025.dat只有149个业务事件日期，不是完整交易日历，首轮发现后停止使用其代表全年覆盖，改以年度附件内真实交易日核验；不把周末近似当官方交易日历。
- CZCE：首页与指定历史入口412，未绕过。DCE：首页503，未获得合法可重跑历史文件。
- SDIC：指定交易提示列表首次200且包含12条有日期链接（标题适用交易日与列表公布日不同），后续列表及最新文章请求503。动态费率表不等于无变化，当前没有已核实费率基线。不得宣称整站不可访问或均无变化。

## 许可、robots与延迟

- MPOB官网All Rights Reserved；交易所数据许可未获得再分发确认。原始HTML/XLSX/完整DB及Parquet默认仅留受控工作区/私有存储，公开PR只给派生覆盖、哈希与诊断图；不公开附件和商业数据。
- NOAA为美国联邦公开数据，公开样例只选必要少量NOAA行，保留来源与基期。
- NASS robots返回User-agent * / Disallow /：后续不自动爬站，正式API/授权bulk尚未配置。UNICA返回202挑战HTML，不能记作可采集，不规避挑战。其他robots的404/503不是使用许可或全站禁止/允许的证明。
- 中国交易所交易日期由官方日结文件提供（包括夜盘归属），不按UTC/自然日切分。MPOB观测月份以月末表述，NOAA为月度。官方公布时刻未核实均NULL，不能用HTTP/ZIP修改时刻推断。
- 本次在08:00后实际取数，不能将它冒充当日07:50已知内容；所有报告显式按冻结时点过滤first seen与official publication。

## 环境与Actions

- Python3.12、Git仓库克隆、依赖安装及官方HTTP真实执行成功。默认沙箱代理连接失败，使用批准的环境网络执行；TLS验证保持开启。
- GitHub PR写权限由连接器确认。workflow_dispatch定义已添加、无schedule；Actions远程运行及S3恢复未实测，无凭据时拒绝仅写临时runner。原08:00ChatGPT任务与独立ENSO研究均未修改。
