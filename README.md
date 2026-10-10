# token-meter · Token 用量与成本看板

**你到底花了多少钱？花在哪个模型上？** 导入日志，一张表看清。

零依赖 · SQLite 存储 · 中文

---

## 数据从哪来

| 来源 | 说明 |
|---|---|
| **relay-proxy 日志**（推荐） | 配合 [relay-proxy](https://github.com/dafeiai918/relay-proxy) 的 `--log requests.jsonl`，自动记录每次调用的 token |
| **CSV 导入** | 表头需含 `model` + `input_tokens`/`prompt_tokens` 等列（兼容各家账单导出） |

---

## 用法

```bash
# 1. 先用 relay-proxy 记录日志
python relay_proxy.py -u https://api.example.com/v1 -k sk-xxx --log requests.jsonl

# 2. 导入
python token_meter.py import -f requests.jsonl

# 3. 看报表（默认按天+按模型）
python token_meter.py report --days 30

# 4. 只看某一个维度
python token_meter.py report --by model
python token_meter.py report --by site

# 5. 导出 CSV
python token_meter.py export -o 用量.csv --days 30

# 6. 设置自定义单价（美元/百万token，输入/输出）
python token_meter.py price --set "my-model:1.5/6"
python token_meter.py price          # 查看当前价目表
```

---

## 输出示例

```
════════════════════════════════════════════════════════════════════════════════════
  token-meter · 按模型统计  近 30 天
════════════════════════════════════════════════════════════════════════════════════
  模型                    调用      输入tokens      输出tokens      花费
────────────────────────────────────────────────────────────────────────────────────
  gpt-4o-mini             412       1,240,300       386,900         $0.4182
  deepseek-chat           88        2,010,500       745,200         $1.3624
  claude-sonnet-4         12        180,000         95,000          $1.9650
────────────────────────────────────────────────────────────────────────────────────
  合计                    512       3,430,800       1,227,100       $3.7456
════════════════════════════════════════════════════════════════════════════════════
```

---

## 价格说明

- 内置一份**参考价目表**（gpt-4o / deepseek-chat / claude / qwen / glm / gemini 等）
- **价格以各家官方为准**，中转站价格通常不同，建议用 `price --set` 设成你实际付的价格
- 匹配规则：先精确匹配模型名，再按子串匹配（`gpt-4o-2026-01-01` 会命中 `gpt-4o`）
- 没有价格的模型仍统计 token，只是不显示金额

---

## 说明

- 数据全部存在本地 `~/.token-meter/`，不上传任何地方
- 重复导入同一份日志不会重复计数（按时间+模型+token数去重）
- 只统计**通过 relay-proxy 的调用**；客户端直连的用量不含在内

---

## 相关项目

| 项目 | 用途 |
|---|---|
| [relay-proxy](https://github.com/dafeiai918/relay-proxy) | 本地代理，产出用量日志 |
| [key-watch](https://github.com/dafeiai918/key-watch) | 余额监控告警 |
| [relay-bench](https://github.com/dafeiai918/relay-bench) | 中转站体检 |

---

## License

MIT
