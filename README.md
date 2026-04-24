# rt_batch_local
本地批量刷新 RT，并生成 Codex 可导入的账号 JSON。脚本会从文本或目录中提取 `rt_*`，逐条刷新，最后把成功、失败、日志和导出文件分开保存，方便后续导入或重试。
## 功能
- 批量提取：递归扫描导入目录，也支持直接粘贴 RT。
- 自动刷新：逐条调用刷新接口，失败时按配置重试。
- Codex 导出：每个账号生成一个独立 JSON 文件。
- 结果归档：保存刷新后的 RT、失败 RT、运行日志和处理明细。
- 自动收尾：全部成功后备份导入源，并清空已处理内容。
## 环境要求
- Python 3.10+
- `requests`
安装依赖：
```powershell
python -m pip install -r requirements.txt
```
如果系统 Python 不允许直接安装依赖，可以使用项目虚拟环境：
```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
.\.venv\Scripts\python.exe rt_batch.py
```
## 快速开始
```powershell
python rt_batch.py
```
首次运行会自动创建这些目录和文件：
- `rt_import/`
- `rt_import/manual_input.txt`
- `rt_import_backup/`
- `codex_output/`
- `refreshed_rts/`
- `failed_rts/`
- `logs/`
如果启动时没有找到 RT，脚本会等待输入。可以直接回车重新扫描，也可以粘贴一条或多条 `rt_*` 后回车处理。
## 输入方式
推荐使用以下任一方式：
1. 编辑 `rt_import/manual_input.txt`
2. 把包含 `rt_*` 的文件放进 `rt_import/`
3. 启动脚本后，在提示符里直接粘贴 `rt_*`
输入内容不要求一行一个 RT。日志、JSON、聊天记录或混合文本都可以，脚本会自动提取其中的 `rt_*`。
兼容旧输入文件：
- `rt_input.txt`
当 `rt_import/` 没有提取到 RT 时，脚本会继续尝试读取 `rt_input.txt`。
## 输出文件
| 路径 | 内容 |
| --- | --- |
| `codex_output/` | Codex 可导入的账号 JSON，每个账号一个文件 |
| `refreshed_rts/` | 本次刷新得到的新 RT 汇总 |
| `failed_rts/` | 刷新失败的原始 RT，方便重试 |
| `rt_output.json` | 每条 RT 的处理状态、错误信息和返回摘要 |
| `logs/` | 运行日志 |
| `rt_import_backup/` | 成功处理后的导入源备份 |
## 运行流程
1. 创建运行目录并扫描输入源
2. 从文本中提取并去重 `rt_*`
3. 按顺序刷新每条 RT
4. 为成功项生成 Codex JSON，并记录新的 RT
5. 为失败项生成重试清单
6. 写入运行日志和 `rt_output.json`
7. 全部成功时备份并清空导入源
## 配置
常用配置在 `rt_batch.py` 顶部：
| 配置 | 说明 |
| --- | --- |
| `CLIENT_ID` | 刷新接口使用的客户端 ID |
| `DELAY` | 每条 RT 之间的等待秒数 |
| `MAX_RETRY` | 刷新失败后的最大重试次数 |
| `RETRY_DELAY` | 重试前的基础等待秒数 |
| `EXPORT_WRITE_RETRY` | 导出文件写入失败后的重试次数 |
| `REQUEST_TIMEOUT` | 单次请求超时时间 |
| `AUTO_CLEANUP_ON_ALL_SUCCESS` | 全部成功后是否备份并清空输入源 |
| `IMPORT_BACKUP_KEEP_BATCHES` | 保留的导入备份批次数 |
| `RESULTS_INCLUDE_INPUT_RT` | `rt_output.json` 是否保留输入 RT |
| `RESULTS_INCLUDE_RAW_RESPONSE` | `rt_output.json` 是否保留接口原始返回 |
| `MASK_CONSOLE_OUTPUT` | 控制台是否脱敏显示 RT 和 token |
默认会在控制台脱敏显示，并在全部成功后清理导入源。导入源会先备份到 `rt_import_backup/`，不会直接丢弃。
## 使用建议
- 平时把 RT 放进 `rt_import/manual_input.txt` 即可；临时测试时，也可以启动后直接粘贴。
- 处理成功后，Codex 导入文件在 `codex_output/`，新的 RT 汇总在 `refreshed_rts/`。
- 如果有失败项，先看控制台提示；需要重跑时，直接使用 `failed_rts/` 里的内容。
- `rt_output.json` 适合排查问题，里面记录了每条 RT 的处理状态、错误信息和返回摘要。
- 账号信息显示是否完整，取决于刷新接口返回的 token 内容。
## License
[MIT](./LICENSE)
