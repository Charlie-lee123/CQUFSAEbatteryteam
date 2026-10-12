"""软件版本号 —— 唯一出处。

发布 / 备份 / git tag 以它为准；main.py 和设置面板都从这里取，
避免入口模块被反向 import（main 一被 import 就会拉起 MainWindow）。
"""

VERSION = "1.7"

OWNER_REPO = "Charlie-lee123/CQUFSAEbatteryteam"

# 仓库地址（设置面板「检查更新」用）。
REPO_URL = f"https://github.com/{OWNER_REPO}"
# GitHub API：标签列表。用 tags 而不是 releases/latest —— 仓库目前只打
# tag、不建 Release，releases/latest 会 404。
REPO_API_TAGS = f"https://api.github.com/repos/{OWNER_REPO}/tags"

# 无代理可直连的版本文件源（依次回退，见 updater.version_sources）。
VERSION_FILE_URLS = (
    f"https://cdn.jsdelivr.net/gh/{OWNER_REPO}@main/version.py",
    f"https://ghfast.top/https://raw.githubusercontent.com/{OWNER_REPO}/main/version.py",
    f"https://gh-proxy.com/https://raw.githubusercontent.com/{OWNER_REPO}/main/version.py",
    f"https://raw.githubusercontent.com/{OWNER_REPO}/main/version.py",
)

# Release 资产名（自动更新下载用）。
ASSET_NAME = "SerialAssistant.exe"

# 当前 Release 里 exe 的 SHA256 —— **由发布脚本在发布时写回本文件**；
# 运行中的旧版本从版本源拉到这里，用来校验下载下来的新 exe。
# 空串 = 该版本发布时尚未记录（自动更新会退回大小/跳过校验）。
LATEST_EXE_SHA256 = "89d58363628bb8414c39ca4d9c9e0ccb78056c376db163623e240f7a8d0ff5c5"

