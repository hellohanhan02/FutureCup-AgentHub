# GitHub / Gitee 私有仓库发布与评委访问

**应该上传 public-source 独立目录，不是上级 FutureCup-Submission。** 上级包含正式比赛文档和内部整理历史，不适合全部公开。当前目录有独立本地仓库，不含旧研发历史或参赛材料仓库历史。

公开前由团队确认项目自身许可证、第三方许可证缺口、提交作者邮箱、源码/证据公开范围。项目没有被擅自授予MIT/Apache许可。可先创建私有空仓库并邀请评委；官方是否接受私有访问需确认。

在 GitHub/Gitee 网页由用户创建空仓库，再在当前公开目录手动执行：

```powershell
git status
git log -1 --oneline
$repoUrl=Read-Host '不含用户名或Token的GitHub/Gitee HTTPS仓库地址'
$u=[Uri]$repoUrl
if($u.Scheme -ne 'https' -or $u.Host -notin @('github.com','gitee.com') -or $u.UserInfo -or $u.Query -or $u.Fragment){throw '仓库URL不安全'}
$existing=git remote get-url origin 2>$null
if($LASTEXITCODE -eq 0){
    if($existing -ne $repoUrl){throw '已有origin不一致，不覆盖或推送'}
}else{git remote add origin $repoUrl}
if($LASTEXITCODE -ne 0){throw '先检查已有remote，不要覆盖后继续push'}
git push -u origin main
```

不要把Token嵌入URL，使用平台凭据管理器。目标私有仓库为 https://github.com/hellohanhan02/FutureCup-AgentHub.git，origin 已配置。发布由团队授权执行；上传结果以该仓库 main 提交与发布报告为准，不把私有仓库描述为无需授权即可公开访问。用无登录窗口核对公开仓库可读；若私有，逐个确认评委访问权限。将实际URL填入报名系统，不能将 localhost 当成公网部署链接。

public-source/.gitignore 排除 .venv、node_modules、.runtime、.validation、.env、SQLite及缓存。以后若有真实运行，不得将未脱敏请求响应或账本数据库提交。ZIP没有Git内部文件；需保留最新源码包SHA256进行核对。