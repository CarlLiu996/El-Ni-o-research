"""Explicit replacement gates; local generation is not scheduler or delivery proof."""
from datetime import timedelta


def assess(store,p0,overview,risk,mode,freeze):
    checks=[]
    def check(name,passed,reason):checks.append(dict(gate=name,passed=bool(passed),reason=reason))
    check('production_cutoff',mode=='production','须以北京时间07:50冻结，演练不能冒充生产')
    check('nine_product_p0',len(overview)==9 and bool(p0) and all(x['status']=='verified' for x in p0),
          f"P0核验 {sum(x['status']=='verified' for x in p0)}/{len(p0)}；过期、冲突和未取得均阻止切换")
    check('research_review',all(x.get('rating_status')=='reviewed' for x in overview),'历史评级基线不能自动成为新判断')
    check('risk_notices_current',risk.get('status')!='unverified','国投最新适用日及公告必须核实')
    check('company_rate_scope',risk.get('baseline_verified',False),'公司/交易所与投机/套保范围不可猜测')
    ops={kind:(status,ref,sha) for kind,status,ref,sha in store.db.execute('''SELECT kind,status,evidence_ref,evidence_sha256
       FROM operational_check WHERE checked_at<=? AND checked_at>=?
       QUALIFY row_number() OVER(PARTITION BY kind ORDER BY checked_at DESC)=1''',[freeze,freeze-timedelta(days=7)]).fetchall()}
    for gate in ['private_storage_restore','scheduled_collection','delivery_verified']:
        r=ops.get(gate)
        check(gate,bool(r and r[0]=='verified' and r[1] and r[2]),'需要最近7天真实执行证据，配置存在或模拟不算通过')
    reviews=store.db.execute("""SELECT DISTINCT p.trade_date FROM parallel_review p JOIN daily_run d USING(run_id)
      WHERE p.status='passed' AND d.mode='production' AND p.baseline_ref<>'' AND p.baseline_sha256<>''
      AND p.reviewer<>'' AND p.trade_date<=? AND p.reviewed_at<=? AND d.freeze_at<=?
      ORDER BY p.trade_date DESC LIMIT 5""",[freeze.date(),freeze,freeze]).fetchall()
    valid=[]
    for (day,) in reviews:
        proof=store.db.execute("""SELECT 1 FROM history WHERE collected_at<=? AND (published_at IS NULL OR published_at<=?)
          AND commodity IN ('RU','NR') AND observation_date=? AND source_id IN ('SHFE','SHFE_ARCHIVE')
          AND data_status IN ('verified','provisional') LIMIT 1""",[freeze,freeze,day]).fetchone()
        if proof:valid.append(str(day))
    check('five_trading_day_parallel_review',len(valid)==5,'至少5个官方交易日证明、原日报基线和人工复核；重复演练不凑数')
    return dict(replacement_ready=all(x['passed'] for x in checks),checks=checks,
      blocking_gates=[x['gate'] for x in checks if not x['passed']],verified_parallel_trade_dates=valid,
      old_0800_task_action='keep_unchanged_until_all_gates_pass')
