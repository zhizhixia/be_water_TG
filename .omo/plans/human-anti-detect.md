# 防真人增强 v2

## TODOs

- [ ] 1. `src/config.py` + `.env.example` — Settings 新增反检测字段
  **agent**: `quick`
  新增: `typing_delay_min: int = 3`, `typing_delay_max: int = 8`, `thinking_delay_min: int = 5`, `thinking_delay_max: int = 25`, `skip_round_pct: int = 10`

- [ ] 2. `ui/send_loop.py` — 四合一实现
  **agent**: `quick`
  打字模拟 + 思考延迟 + 潜水回合 + 长度波动

- [ ] 3. `ui/config_form.py` — UI 字段
  **agent**: `visual-engineering`
  反检测区新增 5 个输入框

## Final
- [ ] F1. pytest 66/66
