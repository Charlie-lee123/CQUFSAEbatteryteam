"""软件版本号 —— 唯一出处。

发布 / 备份 / git tag 以它为准；main.py 和设置面板都从这里取，
避免入口模块被反向 import（main 一被 import 就会拉起 MainWindow）。
"""

VERSION = "1.4"

# 仓库地址（设置面板「检查更新」用）。
REPO_URL = "https://github.com/Charlie-lee123/CQUFSAEbatteryteam"
# GitHub API：标签列表。用 tags 而不是 releases/latest —— 仓库目前只打
# tag、不建 Release，releases/latest 会 404。
REPO_API_TAGS = "https://api.github.com/repos/Charlie-lee123/CQUFSAEbatteryteam/tags"
