# 颖纱客户线索工具

本地运行的、无需模型调用的企业客户线索研究工具。它通过用户已授权的 OpenClaw Chrome 标签页读取公开搜索结果和公开企业页面，并使用 Scrapling 解析页面，帮助整理企业官网、公开商务邮箱和其他公开联系方式。

颖纱独立站：[yingshafabric.ltd](https://yingshafabric.ltd)

## 设计边界

- 运行时不调用模型、付费搜索、代理服务或自动发信/私信。
- 浏览器只使用用户明确授权的 Chrome 标签页，不读取 cookies、密码、storage 或浏览器配置。
- 登录页、验证码、成员名单、私密内容和异常访问页面会暂停或拒绝处理。
- 只保存页面公开的商务信息；缺失的信息不会猜测。
- 数据库保存在本机 Application Support 目录，不应提交到仓库。

## 本地运行

需要 Python 3.11+ 和 uv：

```bash
uv sync
uv run python -m yingsha_leads --open
```

也可以双击 `启动客户线索工具.command`。服务只监听 `127.0.0.1`。

开发测试：

```bash
uv run pytest
```

浏览器烟测需要本机安装 Playwright，并通过 `PLAYWRIGHT_MODULE` 指定模块路径（默认使用 Node 模块解析）：

```bash
PLAYWRIGHT_MODULE=playwright node tests/browser-smoke.cjs
PLAYWRIGHT_MODULE=playwright node tests/capture-smoke.cjs
```

## 项目状态

这是一个面向颖纱内部工作流的本地工具示例。真实客户数据、浏览器配置、OpenClaw 授权信息和安装包不包含在本仓库中。

## License

当前仓库暂未声明开源许可证。未经颖纱授权，请不要将其作为自己的商业软件分发或提供托管服务。
