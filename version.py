"""软件版本号 —— 唯一出处。

发布 / 备份 / git tag 以它为准；main.py 和设置面板都从这里取，
避免入口模块被反向 import（main 一被 import 就会拉起 MainWindow）。
"""

VERSION = "2.5"

OWNER_REPO = "Charlie-lee123/CQUFSAEbatteryteam"

# 仓库地址（设置面板「检查更新」用）。
REPO_URL = f"https://github.com/{OWNER_REPO}"

# ⚠️ 版本文件源（jsDelivr → ghfast/gh-proxy → GitHub 直连的依次回退）**不在这里** ——
#    每条源要带自己的解析器，所以统一放在 ``updater.version_sources()`` 里维护。
#    这里曾经有一份并行的 VERSION_FILE_URLS 常量，早已没人读、还容易和那份真源跑偏，
#    已经删掉；要加源请改 updater。

# Release 资产名（自动更新下载用）。
ASSET_NAME = "SerialAssistant.exe"

# 安装包资产名（第 5 项：安装器接管更新）。Release 里同时挂 exe 与 setup，
# 装了安装版的用户走 setup（能自选目录、自动建快捷方式、有卸载程序），
# 免安装单文件版的用户仍走 exe 静默替换。
SETUP_ASSET_PREFIX = "SerialAssistant-setup-"
SETUP_ASSET_SUFFIX = ".exe"

# 当前 Release 里 exe 的 SHA256 —— **由发布脚本在发布时写回本文件**；
# 运行中的旧版本从版本源拉到这里，用来校验下载下来的新 exe。
# 空串 = 该版本发布时尚未记录 → 自动更新会**明确报错**"服务端没提供本版
#        校验值，请稍后再试"，而不是拿旧哈希硬比、也不是静默跳过校验。
# ⚠️ 升版本时必须先清空成空串：留着**上一版**的哈希会让"拉到的版本文件没解析出
#    哈希"时的兜底校验拿旧哈希去比新包，直接把更新卡死。发布完由脚本写回。
LATEST_EXE_SHA256 = "a5e2ef114f0feebb7cf6564b49c9536f26f8291dbd18ce73abc6fc8f61795761"

# 当前 Release 里**安装包**的 SHA256（同上，由发布脚本写回）。
# 空串 = 尚未记录（同上：更新会明确报错而不是静默跳过）。
LATEST_SETUP_SHA256 = "3d6c55715ecf27427f2aa6d2ed1bb9496e4c157d461bf2e7525b3621c059ca1c"

