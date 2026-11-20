# CAAS：公式与可解释决策

来源 `backend/src/futurecup/application/scheduling.py`、`domain/scheduling.py`，权重与10-A原实验一致，未为比赛材料调参。

硬约束先过滤依赖、Agent启用/可用、Provider、task_type、技能、工具版本与容量。候选不合格则记录原因，不通过评分弥补权限不足。

任务优先级：`P=p+0.1×min(64,后继数)+5×max(0,当前时刻-READY时刻)`，等待按秒计；排序平局用READY时间、task_id。

合格Agent评分：`S=0.30 M+0.25 R+0.20 L+0.10 C+0.15 T`。

- `M=0.5×类型匹配值+0.5×偏好技能覆盖率`；精确类型=1、通配类型=.5，无偏好技能取.5。
- `R=(成功数+5×.5)/(样本数+5)`；无样本经验成功率仍为null，.5仅为评分先验。
- `L=max(0,1-active/capacity)`。
- `C=1/(1+声明相对成本/1)`；未知取.5，不是人民币成本。
- 历史均时延按 `n/(n+5)` 与声明时延（无声明取1000ms）混合；`T=1/(1+预计时延/1000)`，未知取.5。

```text
READY任务按统一P排序
for task in READY:
    profiles = 当前版本、统计、负载快照
    eligible, rejected = 共同硬约束过滤(task, profiles)
    if 无候选: 按配置失败或有界等待
    chosen = 策略.select(eligible)
    记录画像版本、拒绝原因、分项评分和选择
    原子检查任务未认领及Agent容量，并注册Runtime执行
    完成/失败/取消后释放容量，去重记录实际执行统计
```

Fixed按合格候选ID顺序；Round Robin在同一合格集轮询；CAAS最大评分，平局按ID/版本。三策略共用候选、依赖、优先级及容量规则。线上能力观察和simulation来源隔离，取消不冒充成功样本。

10-A数据覆盖四场景、三策略、20种子。B异构与D突发负载有描述性改善，A无优势，C恢复不更快。所有报告配对比较经Holm校正后无显著优势，不可宣传统计显著提升。逻辑毫秒不是实际LLM时延；详见原始数据及报告。
