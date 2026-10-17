# 原版兼容模式的本地数据接口

默认公开模式不需要本页的数据或配置；仅用于显式 `--local` 或提供本地输入参数时。

## 连接现有数据

运行时可指定：

```bash
python3 <skill目录>/scripts/panic_pressure.py --local --data-dir /path/to/selected-backfill --calendar /path/to/rq-bundle/trading_dates.npy --date latest --json
```

或在技能目录创建 `config.local.json`（个人配置不随技能提交）：

```json
{
  "data_dir": "/path/to/selected-backfill",
  "calendar": "/path/to/rq-bundle/trading_dates.npy"
}
```

路径指向既有数据，脚本只读，不复制或分发供应商数据。首次安装到另一台机器仍需配置数据源；安装技能不附送市场数据或行情权限。

## 输入约定

与 microApp `backend/jobs/run_panic_index_backfill.py` 的输出一致：

- 目录中 `manifest.json`：`bundle`（整份行情清单的散列）、`stateCatalog`、`method`、`start`、`end`、`normalization` 等原始字段；不得为绕过校验临时伪造。
- 每日 `YYYY-MM-DD.json`：`tradeDate`、`rawStatus`、`factors.F1...F6`（每项有 `rawValue/canonicalValue/numerator/denominator`）、`coverage`（全市场及三板块）、`exclusions`、`warnings`、`methodHash`、`methodVersion`、`universeVersion`、`sourceVersion`、`historyMode`、`id`、`inputManifestHash`、`contentHash`。已有 `score` 只作复算比对，不作为输出依据。
- 交易日历为NumPy v1一维int64数组，内容为YYYYMMDD整数。脚本用标准库安全读取，不加载pickle。其旁边的 `manifest.json` 必须与输入清单的 `bundle` 绑定，且包含日历的字节数和SHA256。
- 对象散列：`sha256(json.dumps(obj, sort_keys=True, ensure_ascii=False, default=str).encode())`。内容散列排除自身 `contentHash` 字段。日期ID为 `digest({"inputManifest": manifest_hash, "day": date})`。
- 状态 `WARMING_UP` 的历史文件可作为原始参考，但仅在 `rawStatus=READY` 时；不要只取已经出总分的历史文件，否则会额外丢掉252天。

当前适配器只连接该快照协议；其它供应商需要先实现同口径的数据生产器，不支持随意CSV字段猜测。底层 `calculator.py` 保留逐股计算函数供集成测试，但当前CLI仅执行六因子到总分。

## 输出及更新

JSON含 `score`、`scores`（六因子排名）、`dimensions`、`referenceCounts`、`referenceManifest`、原始因子、覆盖率、方法散列、数据版本、数据日、查询日和 `resultHash`。日期差是自然日，不代表缺失交易日数量。`calculationScope=SIX_FACTORS_TO_SCORE` 表明本次计算层级。

默认按最近可计算日出分，并保留 `latestSnapshotDate`；不会生成没有来源的新交易日。指定日期缺失返回 `UNAVAILABLE`，样本不足返回 `WARMING_UP`，分数均为null，进程退出码2。

上游产出新批次后，选定一个版本的完整目录更新配置。当前没有自动联网、自动数据补齐或写回服务；不要称为实时指数。
