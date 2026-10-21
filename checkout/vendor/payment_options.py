"""Observed public payment options; no invented providers or loan terms."""
import re

INSTALLMENT_PLANS = {'花呗分期': (3,6,12), '微信分付': (3,6,12,24),
                    '招商银行': (1,3,6,12,24), '中国建设银行': (1,3,6,12,24),
                    '工商银行': (3,6,12,24)}

def official_options(snapshot):
    if snapshot.get('stage') not in ('PAYMENT','REVIEW'):
        return {'providers':[], 'terms':[]}
    providers=[];terms=[]
    for c in snapshot.get('controls',[]):
        if c.get('disabled'):continue
        label=c.get('label','')
        # Never expose account/card identifiers through configuration metadata.
        if not isinstance(label,str) or not 1<=len(label)<=160 or re.search(r'\d{7,}|@|https?://|\*{3,}',label):continue
        if c.get('group')=='payment' and c.get('installment'):
            if label not in providers:providers.append(label)
        if c.get('group')=='installment_term' and type(c.get('installment_months')) is int:
            n=c['installment_months']
            if 1<=n<=60 and n not in terms:terms.append(n)
    return {'providers':providers[:20], 'terms':sorted(terms)}

def installment_action(snapshot, label, months):
    """Return a unique observed control, verified, or a safe handoff reason."""
    if snapshot.get('stage') == 'REVIEW' and not snapshot.get('payment_choices_present'):
        p = snapshot.get('review_payment')
        if not isinstance(p, dict):
            return None, '最终页分期信息尚未确认，请核对官网；未提交订单'
        if p.get('provider') != label or p.get('months') != months or p.get('fee_percent') != 0:
            return None, '最终页分期渠道、期数或零手续费未匹配，请核对；不会改用其他方案'
        return None, ''
    matches=[c for c in snapshot.get('controls',[]) if c.get('group')=='payment'
             and c.get('installment') and c.get('label')==label and not c.get('disabled')]
    if len(matches)!=1:return None,'官网未提供唯一匹配的分期渠道，请本人选择；不会替换方案'
    provider=matches[0]
    if not provider.get('selected'):return provider,''
    if not snapshot.get('installment_panel_stable'):
        return None,'等待分期渠道与期数面板稳定'
    terms=[c for c in snapshot.get('controls',[]) if c.get('group')=='installment_term'
           and c.get('installment_months')==months and not c.get('disabled')]
    if len(terms)!=1:return None,'官网分期期数尚未唯一确认，请本人选择；不会改成全款'
    # Only an explicitly displayed zero rate is supported for automatic plans.
    # Do not infer it from a comparison/marketing panel elsewhere on the page.
    if not re.search(r'(?<![\d.])0(?:\.0+)?%\s*(?:年化利率|手续费|费率)', terms[0].get('label','')):
        return None,'官网当前分期费率不是已核验的零费率，请本人核对'
    if not terms[0].get('selected'):return terms[0],''
    if snapshot.get('installment_selected_months')!=months:
        return None,'分期期数回读不一致，请核对官网'
    return None,''
