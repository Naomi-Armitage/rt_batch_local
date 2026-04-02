# rt_batch_local

一个本地化的 RT 批量刷新脚本。它直接调用 `https://auth.openai.com/oauth/token` 刷新 RT，本地生成 Codex 可导入 JSON，不再依赖 `fk.accgood.com` 或 Cookie。

## 功能概览

- 直接调用 OpenAI 官方刷新接口
- 本地组装 Codex 可导入 JSON
- 每个 RT 单独导出为一个 JSON 文件
- 每次运行额外生成刷新后 RT 汇总、失败 RT 汇总和日志文件
- 支持批量导入目录递归扫描，自动从任意文本中提取 `rt_*`
- 自动创建目录、交互暂停、手工导入文件、导入源备份与清理
- 结果文件采用原子写入，减少中断时的损坏风险
- 支持导出落盘重试、控制台脱敏显示和按批次保留导入备份

## 依赖

- Python 3.10+
- `requests`

安装依赖：

```powershell
pip install -r requirements.txt
```

## 快速开始

```powershell
python rt_batch.py
```

首次运行时，如果缺少所需目录或没有可处理的 RT，脚本会自动创建这些路径：

- `rt_import/`
- `rt_import/manual_input.txt`
- `rt_import_backup/`
- `codex_output/`
- `refreshed_rts/`
- `failed_rts/`
- `logs/`

然后暂停，等你把任意包含 `rt_*` 的文本放进去再继续。

## 导入方式

推荐直接编辑：

- `rt_import/manual_input.txt`

这个文件不要求一行一个 RT。日志、JSON、聊天记录、混杂文本都可以，脚本会自动提取其中所有 `rt_*`。

也可以把任意包含 `rt_*` 的文件直接丢进：

- `rt_import/`

脚本会递归扫描整个目录。

兼容旧方式：

- `rt_input.txt`

如果 `rt_import/` 里没有提取到 RT，脚本仍会回退读取这个文件。

## 运行流程

1. 检查并创建运行目录
2. 扫描 `rt_import/` 和 `manual_input.txt`
3. 如果没有 RT，则交互暂停，等待补充
4. 逐条调用 `auth.openai.com/oauth/token` 刷新 RT
5. 本地生成 Codex JSON、写入刷新后的 RT、记录 `rt_output.json`
6. 如有失败项，额外输出一份失败 RT 清单
7. 全部成功时自动清理导入源并做批次备份
8. 同步写入运行日志，便于事后排查

## 输出说明

### 1. 账号导出文件

输出到：

- `codex_output/`

文件名示例：

- `codex_example@outlook.com.json`

### 2. 刷新后的 RT 汇总

输出到：

- `refreshed_rts/`

文件名示例：

- `refreshed_rts_20260403_021500.txt`

每次运行一份，一行一个新 RT。

### 3. 失败 RT 汇总

输出到：

- `failed_rts/`

文件名示例：

- `failed_rts_20260403_021500.txt`

处理失败的 RT 会单独汇总，方便再次导入重跑。

### 4. 调试结果

输出到：

- `rt_output.json`

里面会保留每条 RT 的处理状态、刷新接口返回、导出路径、错误信息和可用性诊断。

### 5. 运行日志

输出到：

- `logs/`

文件名示例：

- `rt_batch_local_20260403_021500.log`

### 6. 导入备份

导入源文件会备份到：

- `rt_import_backup/`

批量导入文件会按运行批次进入：

- `rt_import_backup/batch_YYYYMMDD_HHMMSS/`

`manual_input.txt` 和兼容模式下的 `rt_input.txt` 也会单独备份。

## 主要配置

可在 `rt_batch.py` 顶部调整：

- `CLIENT_ID`
- `DELAY`
- `MAX_RETRY`
- `RETRY_DELAY`
- `EXPORT_WRITE_RETRY`
- `REQUEST_TIMEOUT`
- `AUTO_CLEANUP_ON_ALL_SUCCESS`
- `IMPORT_BACKUP_KEEP_BATCHES`
- `RESULTS_INCLUDE_INPUT_RT`
- `RESULTS_INCLUDE_RAW_RESPONSE`
- `MASK_CONSOLE_OUTPUT`

默认情况下：

- `AUTO_CLEANUP_ON_ALL_SUCCESS = True`
- `MASK_CONSOLE_OUTPUT = True`

## 注意事项

- 这个版本不再依赖第三方转换站点
- 运行前不需要 `CF_CLEARANCE` 或 `ACG-SHOP`
- 导出的账号 JSON 是否完全可用，取决于官方返回的 `id_token` 是否包含 `chatgpt_account_id` 和 `chatgpt_plan_type`
- 如果官方返回里缺少这两个 claim，Codex 面板里可能看不到额度或套餐信息
- 当前仓库的第三方依赖只有 `requests`，其余导入均为 Python 标准库模块

## 来源说明

这个本地化版本是基于原 `rt_batch` 的批处理结构改出来的，思路参考了你给的语雀文档《新版GPT账号RT刷新接口》，但实现上改成了本地直连官方刷新接口。

## License

[MIT](./LICENSE)
