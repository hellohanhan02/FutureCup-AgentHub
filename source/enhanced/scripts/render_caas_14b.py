"""Render all 14-B scenarios/policies/ablations from saved JSON; no new measurements."""
import argparse,json
from pathlib import Path
from html import escape
ROOT=Path(__file__).resolve().parents[1]
SCENES=('A_uniform','B_heterogeneous','C_dynamic_fault','D_bursty','E_mixed_types','F_cost_latency','G_cold_start','H_long_recovery')
POLICIES=('fixed','round_robin','caas','adaptive_caas')
COLORS=('#849a94','#527f91','#63895b','#128368')

def render(summary,metric,label,out):
    parts=['<rect width="1500" height="880" fill="white"/>',f'<text x="25" y="30" font-size="21">{escape(label)} -- OFFLINE SIMULATION ONLY</text>',
           '<text x="25" y="54">20 seeds; bars: means; whiskers: unadjusted seed-bootstrap 95% CI. No production/LLM inference.</text>']
    for i,scene in enumerate(SCENES):
        x=(i%2)*740+25;y=(i//2)*192+92
        groups=[next(g for g in summary['groups'] if g['scenario']==scene and g['variant']==policy) for policy in POLICIES]
        maximum=max(g['metrics'][metric]['ci95'][1] for g in groups)*1.03 or 1
        parts.append(f'<text x="{x}" y="{y}" font-size="16">{scene}</text>')
        for j,(policy,g) in enumerate(zip(POLICIES,groups)):
            yy=y+20+j*30;value=g['metrics'][metric]['mean'];lo,hi=g['metrics'][metric]['ci95'];scale=425/maximum;xx=x+115
            parts += [f'<text x="{x}" y="{yy+11}">{policy}</text>',f'<rect x="{xx}" y="{yy}" width="{value*scale:.3f}" height="15" fill="{COLORS[j]}"/>',
                f'<path d="M {xx+lo*scale:.3f} {yy+7} H {xx+hi*scale:.3f} M {xx+lo*scale:.3f} {yy+3} V {yy+12} M {xx+hi*scale:.3f} {yy+3} V {yy+12}" stroke="#102e26" fill="none"/>',f'<text x="{x+565}" y="{yy+11}">{value:.4f}</text>']
    svg='<svg xmlns="http://www.w3.org/2000/svg" width="1500" height="880"><g font-family="sans-serif" font-size="12" fill="#173e36">'+''.join(parts)+'</g></svg>'
    out.write_text(svg,encoding='utf-8')


def main():
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('--summary',default='results/caas-14b/evaluation/summary.json');parser.add_argument('--output',default='results/caas-14b/figures');args=parser.parse_args()
    source=(ROOT/args.summary).resolve();out=(ROOT/args.output).resolve()
    if not source.is_relative_to(ROOT) or not out.is_relative_to(ROOT) or out.exists():raise ValueError('Use a new output directory in the clone')
    summary=json.loads(source.read_text('utf-8'));out.mkdir(parents=True)
    for m,label in [('success_rate','Success rate'),('mean_latency_ms','Mean completion latency (logical ms)'),('p95_latency_ms','P95 completion latency (logical ms)'),('total_simulated_cost','Artificial relative total cost')]:render(summary,m,label,out/(m+'.svg'))
    lines=['# Mechanism ablations: removed variant MINUS full adaptive','', 'No significance claim for these exploratory ablations. All scenarios included.', '',
        '| Scenario | Removed mechanism | Success delta pp | Mean latency delta logical ms | Cost delta |','|---|---|---:|---:|---:|']
    for scene in SCENES:
        rows={g['variant']:g['metrics'] for g in summary['groups'] if g['scenario']==scene};full=rows['adaptive_caas']
        for option in ('no_weights','no_recency','no_health','no_attribution'):
            m=rows[option];lines.append(f"| {scene} | {option} | {(m['success_rate']['mean']-full['success_rate']['mean'])*100:+.4f} | {m['mean_latency_ms']['mean']-full['mean_latency_ms']['mean']:+.4f} | {m['total_simulated_cost']['mean']-full['total_simulated_cost']['mean']:+.4f} |")
    (out/'ablation-effects.md').write_text('\n'.join(lines)+'\n',encoding='utf-8')
    print('Rendered all four policies / eight scenarios / four metrics, plus ablation differences.')
if __name__=='__main__':main()
