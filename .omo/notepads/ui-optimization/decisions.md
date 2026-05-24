
使用 callback 模式而非轮询：send_loop 完成轮次后主动回调，state.paused 仍由 app.py 的 on_state_changed 控制
