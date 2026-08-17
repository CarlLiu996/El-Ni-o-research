# 基本面数据契约

基本面文件放在 `data/processed/fundamentals/`，文件名必须与 `config/monitoring.json` 的 `dataset` 一致。

最低字段：

| 字段 | 类型 | 含义 |
|---|---|---|
| `date` | `YYYY-MM-DD` | 指标所属期或发布日期，必须明确口径 |
| `value` | number | 原始指标值，不预先转成字符串 |
| `unit` | string | 例如 `tonnes`、`pct`、`tonnes_per_hectare` |
| `source` | string | 来源机构或公开 URL |
| `published_at` | `YYYY-MM-DD` | 实际可获知日期，用于防止前视偏差 |
| `vintage` | string | 数据版本或抓取时间，支持修订审计 |

可选字段：`region`、`country`、`crop_year`、`note`。

示例：

```csv
date,value,unit,source,published_at,vintage
2026-07-31,0,tonnes,replace-with-source,2026-08-10,2026-08-10
```

示例中的 `0` 只是字段演示，不得作为真实研究输入。正式导入必须保留原始来源和实际发布日期；监控与回测使用 `published_at` 决定信息何时可用。

当前必需文件：

- `mpob_production.csv`
- `mpob_inventory.csv`
- `mpob_exports.csv`
- `ffb_yield.csv`
- `india_sugar_output.csv`
- `unica_crush.csv`
- `unica_sugar_mix.csv`
- `global_sugar_stock_use.csv`

若文件缺失或超过新鲜度阈值，基本面层保持 `INCOMPLETE`，整体状态不得升级为配置复核。
