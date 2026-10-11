"""Run the actual language-model research agent (or explicitly replay its decisions)."""
from pathlib import Path
import argparse,json,hashlib
from agent_runtime import CodexCLIProvider,ReplayProvider,ResearchController
from tools.toolkit import ResearchToolkit,TOOL_SPEC
from tools.common import digest,save

GOAL='''研究用户材料HarmonyOS第七期第1页提出的众包离线高精度位置聚合开放问题的可计算子问题：
在历史测量、距离异常与运动模型失配下，发展更稳健的当前位置估计，并研究可校准的不确定性。
你是实际科研决策者，必须根据工具返回的真实证据调整假设。请用中文写analysis_summary、hypothesis、memory_note。
先了解来源和已有工具的数学定义，建立基线，随后自行决定实验顺序。
必须体现实质研究：提出至少3个可检验假设；比较不同残差建模或观测权重，以及运动模型选择机制；
至少注册3个组成有区别的方法；依据诊断修订至少一个方案；运行至少一次控制消融；执行数学命题检查。
这不是固定候选列表搜索，不需要证明必胜；保留拒绝假设、失败个案及负面结果。
验证集只允许4次工具调用，请将值得确认的方法集中验证。先探索discovery再validation，测试/校准集从未暴露给你。
不要把开发均值下降当作泛化保证。关注正常静止场景不劣化和移动异常场景，同时报告计算成本。
数据合成不等于工业验收，有限实验不等于证明，源码中预实现的算子不能声称由你发明。
最终冻结一个有validation证据的方法，并清楚说明未解决部分。预算足够但有限，建议在20-24步内完成。
只调用声明的研究工具（在结构化最终响应中给出action），不要调用Codex自身的shell/web/file工具。'''

def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--output',type=Path,default=Path('runs/live_reference'))
    p.add_argument('--resume',action='store_true')
    p.add_argument('--provider',choices=['codex','replay'],default='codex')
    p.add_argument('--replay-source',type=Path)
    p.add_argument('--timeout',type=float,default=300)
    p.add_argument('--reasoning-effort',default='medium')
    p.add_argument('--max-steps',type=int,default=26)
    p.add_argument('--max-experiments',type=int,default=10)
    p.add_argument('--max-model-calls',type=int,default=38)
    args=p.parse_args();root=Path(__file__).resolve().parent
    if args.output.exists() and not args.resume: p.error('Use a new output directory or --resume to preserve evidence')
    args.output.mkdir(parents=True,exist_ok=True)
    toolkit=ResearchToolkit(root,args.output/'tools',resume=args.resume)
    if args.provider=='codex':
        provider=CodexCLIProvider(timeout=args.timeout,cwd=root,reasoning_effort=args.reasoning_effort)
    else:
        if not args.replay_source:p.error('--replay-source is required for replay')
        provider=ReplayProvider(args.replay_source)
    source_paths=(sorted((root/'localization').glob('*.py'))+sorted((root/'tools').glob('*.py'))
                  +sorted((root/'agent_runtime').glob('*.py'))+[Path(__file__).resolve(),root/'benchmark.py'])
    source_hashes={str(f.relative_to(root)).replace('\\','/'):hashlib.sha256(f.read_bytes()).hexdigest() for f in source_paths}
    config={'study':'CrowdLocateLab-v2','discovery_seed':46191,'validation_seed':57383,
            'source_hashes':source_hashes,'reasoning_effort':args.reasoning_effort,
            'test_access':False,'goal_sha256':hashlib.sha256(GOAL.encode()).hexdigest()}
    if not args.resume:save(args.output/'protocol.json',config)
    controller=ResearchController(provider,toolkit,args.output,GOAL,TOOL_SPEC,config,
                seed_hash=digest({'discovery':46191,'validation':57383}),
                max_steps=args.max_steps,max_experiments=args.max_experiments,
                max_model_calls=args.max_model_calls,critic_every=3,resume=args.resume)
    state=controller.run()
    print(json.dumps({'status':state.get('status'),'output':str(args.output),'steps':state.get('steps')},ensure_ascii=False))

if __name__=='__main__': main()
