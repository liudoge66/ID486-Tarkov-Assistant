"""Human PMC journal and versioned Fence JSONL, both with explicit local time."""
from datetime import datetime
import json, threading, time, uuid

NAMES = {
    'network_candidate_end_hold_ack':'已确认结束请求保持，尚未判定最终死亡',
    'network_candidate_end_revoke_requested':'正常画面已反证结束请求保持，等待解除确认',
    'network_candidate_end_revoke_ack':'已确认解除结束请求保持，恢复联网待命',
    'network_candidate_status':'提前隔离证据状态',
    'transfer_hint_cancelled':'已清除失去依据的转移提示',
    'death_exit_timing':'大退耗时与1秒风险线审计',
    'network_candidate_visual':'可信死亡候选画面审计',
    'network_candidate_requested':'可信死亡候选已请求临时隔离',
    'network_candidate_ack':'临时隔离规则已提交（阻断覆盖另行核验）',
    'network_candidate_cancelled':'死亡候选已撤销，恢复联网',
    'network_candidate_released':'临时隔离规则已解除',
    'network_candidate_end_hold':'死亡候选期间发现结束请求，保持隔离',
    'settlement_end_retry':'游戏结束请求自动重试记录',
    'settlement_end_response':'游戏结束请求响应或网络失败记录',
    'center_extraction_countdown':'中下部撤离倒计时读取',
    'network_pulse_retry':'联网保险心跳写入暂不可用，继续重试',
    'network_auto_recover_scheduled':'断网保险已安排自动恢复',
    'network_auto_recover_start':'断网保险正在自动恢复',
    'relative_reference_status':'相对渐黑参考检查',
    'relative_death_confirmed':'HUD消失与相对渐黑共同确认死亡',
    'extraction_candidate_started':'撤离渐黑待确认（继续监控）',
    'extraction_candidate_cancelled':'撤离候选已撤销（继续监控）',
    'full_review_frame_retained':'已有完整画面，继续本帧复核而不整轮重采',
    'game_end_request_summary':'本轮游戏结束请求观察汇总',
    'network_prepare_candidate':'可疑画面已请求网络资源预备（未断网）',
    'network_prepare_requested':'网络资源预备命令已发送（未断网）',
    'network_prepare_ack':'联网助手资源预备结果（未断网）',
    'network_prepare_error':'网络预备未确认，保留确认时的原隔离路径',
    'hud_continuity_audit':'新帧HUD持续观察结束审计',
    'frame_review_veto':'新帧恢复HUD或界面状态改变，撤销旧候选',
    'capture_backend_ready':'采集方式与显示器色彩已确认',
    'capture_color_changed':'显示器色彩变化，已清空旧画面判断',
    'esc_title_audit':'ESC标题复核记录',
    'esc_title_audit_error':'ESC诊断保存失败',
    'esc_capture_requested':'ESC扩展画面复核已请求',
    'esc_capture_restored':'ESC复核已返回战斗范围',
    'esc_visual_state':'ESC画面识别状态',
    'esc_title_error':'ESC标题检测异常',
    'esc_death_fade_confirmed':'ESC界面死亡渐黑已确认',
    'steam_overlay_waiting':'Steam特征暂被遮挡，等待确认游戏界面',
    'steam_overlay_paused':'Steam叠加层已打开，暂停画面判断',
    'steam_overlay_resumed':'Steam叠加层已关闭，恢复画面判断',
    'btr_dialogue_state':'BTR对话界面状态变化',
    'settlement_watch_stopped':'结算日志监听结束审计',
    'settlement_parallel_confirmed':'结算日志与独立新画面共同确认死亡',
    'settlement_parallel_result':'独立快速检查结果',
    'settlement_parallel_exit_deferred':'快速退出请求未被接受，保留原路径备用退出',
    'settlement_watch_started':'结算日志辅助通道已待命',
    'settlement_watch_error':'结算日志辅助通道不可用，画面判断继续',
    'settlement_log_seen':'发现新的战局结束请求，等待画面确认',
    'settlement_log_stale':'忽略过期结算日志',
    'settlement_fast_probe':'结算请求已唤醒快速画面确认',
    'settlement_visual_confirmed':'结算日志与连续HUD消失共同确认死亡',
    'startup':'程序启动', 'armed':'死亡监控已开启', 'disarmed':'死亡监控已停止',
    'arm_rejected':'开启失败：未满足界面条件', 'hotkey_received':'收到监控快捷键',
    'death_decision':'已判定死亡', 'trigger':'死亡处理完成',
    'process_termination_request':'结束指定游戏进程的请求结果',
    'input_delivery':'备用退出请求发送结果', 'direct_exit_fallback':'已使用备用退出方式',
    'exit_observation':'游戏进程退出检查', 'game_window_hidden':'游戏窗口已隐藏',
    'game_window_gone':'游戏窗口已消失', 'extraction_transition':'已识别正常撤离过渡',
    'status_feedback':'状态提示已发出', 'diagnostic_saved':'现场截图已保存',
    'diagnostic_error':'现场截图保存失败', 'diagnostic_pruned':'已清理旧截图',
    'performance':'采集与检测性能统计', 'absence_reset':'界面恢复，清除死亡计时',
    'focus_paused':'已切出游戏，暂停画面判断', 'focus_resumed':'已切回游戏，恢复画面判断',
    'capture_released':'屏幕采集已释放', 'capture_stalled':'暂无新画面',
    'transfer_resume_feedback':'已确认新地图HUD，发出恢复提示',
    'transfer_manual_rearm_needed':'转移过程未完整观察，请重新开启',
    'transfer_return_unobserved':'切屏期间的转移过程未确认',
    'display_switch_guard_started':'正在切换显示设置，暂停死亡判断',
    'settings_transition_resumed':'显示设置切换完成，恢复监控',
    'network_state':'联网控制状态变化', 'network_evidence':'游戏联网证据',
    'network_error':'联网控制失败，保持联网', 'network_death':'联网控制收到死亡事件',
    'network_death_hold_ack':'已确认死亡后保留隔离规则',
    'network_reconnect_started':'游戏已解除隔离，开始核实联网',
    'network_reconnect_complete':'重连空窗结束，隔离规则重新建立',
    'network_reconnect_timeout':'重连等待超时，保持联网并继续等待有效响应',
    'rehearsal_mode_changed':'演练模式已切换',
    'network_restore_scheduled':'根据保活周期提前安排恢复，未延长隔离上限',
    'network_state':'死亡断网保险状态',
    'network_death_cutoff_error':'死亡隔离请求失败，继续执行游戏退出',
    'network_status_read_retry':'网络状态暂不可读，保留规则并重试',
    'network_death_hold_lost':'死亡保持状态丢失，重新请求隔离',
    'network_death_ack_delayed':'死亡隔离确认延迟，未请求解除',
    'network_diagnostic_started':'已手动请求15秒独立后端验证',
    'network_diagnostic_finished':'独立后端验证结束，已记录恢复结果',
    'network_backend_probe':'后端客户端请求记录，仅作通信线索',
    'network_probe_error':'后端请求记录读取失败，规则未改变',
    'network_expected_session_error':'全网络封锁期间的游戏报错，规则继续保持',
    'network_policy_context':'已创建临时独立高优先级子层',
    'network_policy_readback':'已从系统读回过滤器部署结果',
    'network_policy_status':'网络策略覆盖与审计状态',
    'network_arbitration_snapshot':'只读仲裁候选快照，未证明实际裁决',
    'network_policy_audit_error':'策略审计未完成，现有规则保持',
    'network_policy_released':'自有临时策略随会话关闭清理',
    'network_restore':'已请求恢复游戏联网', 'network_broker':'限时网络助手状态',
    'network_permission_changed':'联网控制待命许可已改变', 'network_manual_start':'手动快捷键已启动本轮联网控制',
    'preferences_restored':'已恢复保存的选项，运行状态保持待命', 'settings_saved':'用户设置已保存',
    'network_log_source':'已选择游戏联网日志文件', 'network_wait':'正在等待新的联网证据，尚未隔离',
    'network_command':'已写入联网助手控制命令', 'network_targets':'已核对隔离域名与目标IP',
    'network_rules':'已建立指定游戏的隔离规则', 'network_restore_ack':'已确认联网规则清理完成',
    'network_block_hit':'已观察到本工具规则拦截连接', 'network_hit_monitor':'系统拦截事件订阅状态',
    'network_hit_summary':'本轮连接拦截事件统计', 'network_rules_removed':'本轮隔离规则已移除',
    'network_broker_audit':'独立联网助手审计记录',
    'network_audit_error':'读取联网审计记录失败',
    'network_targets_verified':'独立助手已核对域名解析与请求屏蔽目标',
    'network_first_isolation':'手动开启后立即请求首轮隔离',
    'network_expected_transport_error':'隔离期间观察到连接失败，继续遵守恢复截止时间',
    'video_test_feedback':'录像测试结果与音效反馈', 'exit_action_changed':'自动大退开关已改变',
}
PMC_EVENTS = {'death_exit_timing','startup','armed','disarmed','arm_rejected','death_decision','trigger',
              'process_termination_request','input_delivery','exit_observation','extraction_transition',
              'diagnostic_saved','diagnostic_error','status_feedback','exit_action_changed',
              'network_permission_changed','network_manual_start','network_state','network_death',
              'network_death_hold_ack','network_restore_ack','network_error','network_death_cutoff_error'}
PMC_EVENTS.update(kind for kind in NAMES if kind.startswith(('settlement_','network_candidate_','transfer_')))
PMC_EVENTS.update(kind for kind in NAMES if kind.startswith('esc_'))
PMC_EVENTS.update(('steam_overlay_paused','steam_overlay_resumed','steam_overlay_waiting','btr_dialogue_state'))
PMC_EVENTS.update(('relative_reference_status','relative_death_confirmed','extraction_candidate_started','extraction_candidate_cancelled','capture_backend_ready','capture_color_changed','frame_review_veto','hud_continuity_audit','full_review_frame_retained','game_end_request_summary','network_prepare_candidate','network_prepare_requested','network_prepare_ack','network_prepare_error'))

def stamp(ns=None):
    return datetime.fromtimestamp((ns if ns is not None else time.time_ns())/1e9).astimezone().isoformat(timespec='milliseconds')

class EventJournal:
    def __init__(self, stream, fence, jsonl_stream=None):
        self.stream=stream;self.fence=fence;self.jsonl_stream=jsonl_stream;self.lock=threading.Lock();self.sequence=0
        self.session=uuid.uuid4().hex;self.wall=time.time_ns();self.mono=time.perf_counter_ns()

    def _format_jsonl(self, kind, name, details, wall, mono):
        record=dict(schema_version=2,session_id=self.session,sequence=self.sequence,
                    timestamp=stamp(wall),time=wall/1e9,unix_time_ns=wall,monotonic_ns=mono,
                    event=kind,event_name=name,details=details)
        record.update({k:v for k,v in details.items() if k not in record})
        for key,value in details.items():
            if key.endswith('_monotonic') and isinstance(value,(int,float)):
                record[key+'_estimated_time']=stamp(self.wall+int(value*1e9)-self.mono)
            elif key.endswith('_wall_ns') and isinstance(value,int):record[key+'_time']=stamp(value)
        return json.dumps(record,ensure_ascii=False,default=str)+'\n'

    def write(self, kind, details, detailed=False):
        if kind.startswith('transfer_'):PMC_EVENTS.add(kind)
        if not self.fence and kind not in PMC_EVENTS and not (detailed and kind in ('performance','focus_paused','focus_resumed','capture_stalled','game_window_hidden','game_window_gone')):return
        if not detailed and kind in ('performance','absence_reset'):return
        wall=time.time_ns();mono=time.perf_counter_ns();name=NAMES.get(kind,kind.replace('_',' '))
        with self.lock:
            self.sequence+=1
            if self.jsonl_stream is not None and not self.fence:
                try:self.jsonl_stream.write(self._format_jsonl(kind,name,details,wall,mono))
                except OSError:pass
            if self.fence:
                self.stream.write(self._format_jsonl(kind,name,details,wall,mono))
            else:
                important=[]
                if kind.startswith('transfer_') or kind=='extraction_candidate_cancelled':
                    important.append('判定细节：'+json.dumps(details,ensure_ascii=False,default=str))
                if kind.startswith('network_candidate_') or kind in ('settlement_end_retry','settlement_end_response','death_exit_timing'):
                    important.append('证据：'+json.dumps(details,ensure_ascii=False,default=str))
                    # Structured measurements survive the compact PMC journal.
                    if getattr(self.stream,'name',None):
                        from pathlib import Path
                        with (Path(self.stream.name).parent/'candidate_network_events.jsonl').open('a',encoding='utf-8') as audit:
                            audit.write(json.dumps(dict(timestamp=stamp(wall),unix_time_ns=wall,event=kind,**details),ensure_ascii=False,default=str)+'\n')
                if kind in ('extraction_transition','relative_death_confirmed','relative_reference_status'):
                    important.append('判定依据：'+json.dumps(details.get('evidence'),ensure_ascii=False,default=str))
                for key,label in [('value','下方倒计时秒数'),('confidence','数字置信度'),('roi','观察区域'),('mapping','视口适配方式'),('reference_preserved','扩大范围时保留存活参考'),('build','内部构建'),('source_log_path','游戏结束请求原始日志'),('request_route','请求路径'),('found','观察到结束请求'),('prepared','资源已预备'),('filter_count','当前规则数'),('visual_path','画面检查路径'),('checked_frames','持续观察帧数'),('errors','观察异常次数'),('backend','采集方式'),('source_format','原始像素格式'),('hdr','HDR已启用'),('white_scale','白色参考比例'),('displays','显示器色彩状态'),('configuration_generation','色彩配置编号'),('reason','原因'),('result','结果'),('status','提示'),('pid','游戏进程号'),
                                  ('base_menu','固定菜单分数'),('title_present','标题存在'),('title_boxes','文字框数量'),
                                  ('capture_region','采集范围'),('frame_size','输入尺寸'),('frame_sequence','采集帧编号'),('action','判断动作'),
                                  ('state','检查结果'),('elapsed_ms','请求后经过毫秒'),('delay_ms','死亡确认延时毫秒'),
                                  ('path','截图位置'),('dry_run','演练模式'),
                                  ('source_timestamp','游戏日志时刻'),('log_lag_ms','日志可读延迟毫秒'),
                                  ('capture_after_read_ms','读到日志至确认帧采集毫秒'),('read_to_exit_ms','读到日志至退出请求毫秒'),('request_id','结算请求编号'),
                                  ('call_ms','退出接口调用毫秒'),('analysis_ms','本帧分析毫秒'),
                                  ('read_to_visual_ms','日志读到至独立检查结束毫秒'),
                                  ('session','游戏日志会话'),('files','跟踪日志文件'),
                                  ('read_bytes','尾读累计字节'),('matched_requests','结束请求匹配次数'),
                                  ('valid_hints','有效提示次数'),('unread_tail_bytes','停止时未读尾部字节')]:
                    if key in details:
                        value=details[key]
                        if kind=='game_end_request_summary':
                            if value is None:continue
                            if key=='found':value='已记录到' if value else '未记录到（不等于一定未发送）'
                        if kind=='relative_reference_status' and key=='reason':
                            value={'reference_missing':'没有已保存的有效参考画面',
                                   'reference_expired':'参考画面超过1秒有效期',
                                   'reference_not_older_than_frame':'当前帧时刻未晚于参考画面',
                                   'reference_has_no_structure':'参考画面缺少可比较结构',
                                   'context_guarded':'当前界面或过渡状态不使用快通道',
                                   'scene_structure_changed':'场景结构发生变化',
                                   'watching_relative_fade':'正在观察相对变暗',
                                   'waiting_fresh_relative_frames':'等待新的完整复核及连续渐黑证据',
                                   'relative_fade_confirmed':'相对渐黑已确认',
                                   'hsv_fade_confirmed':'HSV褪色与渐黑已确认',
                                   'dark_trend_confirmed':'暗区扩大与持续渐暗已确认',
                                   'dark_trend_waiting':'渐暗程度已满足，等待新的复核和连续趋势',
                                   'dark_trend_insufficient':'暗区扩大或持续渐暗程度尚未满足',
                                   'dark_trend_reference_unavailable':'缺少有效的存活亮度参考',
                                   'dark_trend_context_guarded':'当前界面或过渡状态不使用渐暗趋势通道',
                                   'hsv_context_guarded':'当前界面或过渡状态不使用HSV快通道',
                                   'hsv_hud_or_menu_visible':'HUD或菜单仍可见，HSV连续证明已重置',
                                   'hsv_reference_unavailable':'缺少有效的存活颜色参考',
                                   'hsv_insufficient_fade':'褪色或渐黑程度尚未同时满足条件',
                                   'hsv_waiting_fresh_frames':'等待新的完整复核及HSV连续变化证据',
                                   'current_frame_ui_visible':'当前帧出现其他有效界面',
                                   'new_text_or_panel_ambiguity':'当前帧有文字或面板歧义'}.get(value,value)
                        if kind.startswith('extraction_candidate_') and key=='reason':
                            value={'near_end_menu_structural_fade':'倒计时接近结束，菜单结构保留并整体渐暗',
                                   'near_end_hud_structural_fade':'倒计时接近结束，HUD与场景结构保留并整体渐暗',
                                   'interface_changed':'出现新界面，原渐黑候选作废',
                                   'transition_timer_expired_or_brightened':'界面切换、倒计时失效、画面回亮或候选超时'}.get(value,value)
                        if kind.startswith('esc_') and key=='reason':
                            value={'not_expanded':'尚未请求扩展画面','combat_hud_clear':'战斗HUD明确，无需标题检测',
                                'legacy_menu_confirmed':'固定菜单已确认，无需标题检测','transfer_protected':'转移保护中，不走标题分支',
                                'settlement_fast_candidate':'本帧进入结算快速候选','awaiting_fresh_expanded_frame':'等待新的扩展画面复核',
                                'needs_expanded_capture':'输入范围不足，继续扩大采集','title_text_and_layout':'标题文字与布局符合',
                                'certified_black_scene':'已核对近黑画面，无需标题搜索','no_title_layout':'文字框或布局未满足标题条件','model_exception':'标题模型执行异常'}.get(value,value)
                        if key=='action':value={'death':'死亡候选','monitor':'继续监控','uncertain':'暂无法确认','transfer_wait':'等待转移'}.get(value,value)
                        important.append(label+'：'+str(value))
                if 'request_wall_ns' in details:important.append('退出请求时刻：'+stamp(details['request_wall_ns']))
                if 'seen_wall_ns' in details:important.append('读到日志时刻：'+stamp(details['seen_wall_ns']))
                if 'frame_wall_ns' in details and details['frame_wall_ns'] is not None:important.append('采集时刻：'+stamp(details['frame_wall_ns']))
                for key,label in [('checks_per_second','每秒检测次数'),('analysis_ms_p95','95%检测耗时毫秒'),('capture_ms_p95','95%采集耗时毫秒'),('process_cpu_one_core_percent','占单核CPU百分比'),('esc_fast_frames','ESC快速检测帧数'),('esc_fast_ms_p50','ESC快速检测中位耗时毫秒'),('death_fast_frames','死亡画面快速检查帧数'),('death_fast_ms_p50','死亡画面快速检查中位耗时毫秒'),('parallel_ui_frames','并行界面复核帧数'),('parallel_ui_ms_p50','并行界面复核中位耗时毫秒'),('title_reuses','同帧标题复核复用次数'),('newer_frame_vetoes','新帧撤销旧候选次数')]:
                    if key in details:important.append(label+'：'+str(details[key]))
                states={'exited':'已退出','pending':'尚未退出，继续观察','still_running':'仍在运行','unavailable':'无法确认','requested':'请求已提交'}
                text='；'.join(important)
                for original,translated in states.items():text=text.replace('检查结果：'+original,'检查结果：'+translated)
                self.stream.write('['+stamp(wall)+'] '+name+('｜'+text if text else '')+'\n')

PMC_EVENTS.update(('network_pulse_retry','network_auto_recover_scheduled','network_auto_recover_start'))

PMC_EVENTS.add('center_extraction_countdown')
