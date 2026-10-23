"""登录后联系人页不显示邮箱：自动选购（固定门店）和手动开始（可换同城门店）走同一套核对。"""
import copy
import unittest

from worker import core  # 先导入 worker：它会把 vendor 目录加进导入路径
from checkout_engine import DEFAULTS, Attempt, validate_preferences, variants

CHOICES = ('fixed', 'same_city')


def attempt(choice):
    p = validate_preferences(dict(DEFAULTS, fulfillment='pickup', financing='full', payment='支付宝',
                                  store_id='R683', pickup_date_policy='earliest'))
    p['store_choice'] = choice
    a = Attempt(p, variants(p)[0], 0)
    a.bag_verified = a.fulfillment_verified = True
    a.started = 0
    a.pickup_confirmed_dates = ['2026-10-23']
    return a


def contact(fields=('family_name', 'given_name', 'phone', 'id_last4'), buttons=1, **extra):
    s = dict(stage='CONTACT', fingerprint='contact', contact_email_defer_allowed=True,
             fields=[dict(key=k, ref=k, matches=True, filled=True, configured=True) for k in fields],
             controls=[dict(ref='next%d' % i, label='继续选择付款方式', role='button', disabled=False)
                       for i in range(buttons)])
    s.update(extra)
    return s


def review(email, **extra):
    s = dict(stage='REVIEW', fingerprint='review-' + email, controls=[], fields=[], final_button=True,
             review_extras_match=True, review_target_matches=True, review_quantity=1, review_method='pickup',
             review_store_matches=True, review_applecare=False, total=9999, review_full_payment='支付宝',
             errors=False, review_contact_email=email, review_contact_email_matches=email == 'match',
             review_items=[dict(target_matches=True, quantity=1, method='pickup', store_matches=True,
                                pickup_dates=['2026-10-23'])])
    s.update(extra)
    return s


def through_contact(choice):
    a = attempt(choice)
    d = a.decide(contact(), 1)
    a.payment_verified = True  # 付款那一步照常核对，这里直接当作已核对
    return a, d


class ContactEmailTests(unittest.TestCase):
    def test_missing_email_box_continues_to_payment_for_auto_and_manual_runs(self):
        for choice in CHOICES:
            with self.subTest(choice=choice):
                a, d = through_contact(choice)
                self.assertEqual((d.kind, d.reason), ('click', 'continue_CONTACT'))
                self.assertTrue(a.contact_email_deferred)
                self.assertFalse(a.contact_verified, '邮箱还没核对，到核对页再看')

    def test_review_without_any_email_is_ready_by_the_signed_in_account(self):
        for choice in CHOICES:
            with self.subTest(choice=choice):
                a, _ = through_contact(choice)
                d = a.decide(review('none'), 2)
                self.assertEqual(d.kind, 'ready')
                self.assertIn('官网未显示联系邮箱', d.reason)

    def test_review_with_the_saved_email_is_ready_without_a_note(self):
        for choice in CHOICES:
            with self.subTest(choice=choice):
                a, _ = through_contact(choice)
                d = a.decide(review('match'), 2)
                self.assertEqual(d.kind, 'ready')
                self.assertNotIn('邮箱', d.reason)

    def test_review_showing_another_email_stops_without_ordering(self):
        for choice in CHOICES:
            with self.subTest(choice=choice):
                a, _ = through_contact(choice)
                d = a.decide(review('mismatch'), 2)
                self.assertEqual(d.kind, 'attention')
                self.assertIn('与保存资料不一致', d.reason)

    def test_fresh_read_before_submitting_checks_the_email_again(self):
        # 提交前会复制一份重新判断新读到的页面：邮箱这时冒出来且对不上，也不能下单。
        for choice in CHOICES:
            with self.subTest(choice=choice):
                a, _ = through_contact(choice)
                self.assertEqual(a.decide(review('none'), 2).kind, 'ready')
                check = copy.deepcopy(a)
                check.terminal = None
                self.assertEqual(check.decide(review('mismatch'), 3).kind, 'attention')

    def test_final_button_before_review_page_is_not_trusted(self):
        a, _ = through_contact('fixed')
        paid = [dict(ref='pay', label='支付宝', role='radio', group='payment', selected=True, disabled=False)]
        d = a.decide(review('none', stage='PAYMENT', controls=paid), 2)
        self.assertEqual(d.kind, 'attention')
        self.assertIn('还没到订单核对页', d.reason)

    def test_contact_page_with_an_email_box_or_doubtful_buttons_is_not_deferred(self):
        for choice in CHOICES:
            with self.subTest(choice=choice):
                a = attempt(choice)
                s = contact()
                s['fields'].append(dict(key='email', ref='email', matches=False, filled=False, configured=True))
                d = a.decide(s, 1)
                self.assertEqual((d.kind, d.reason), ('fill', 'fill:email'), '有邮箱框时照常填写并回读')
                a = attempt(choice)
                d = a.decide(contact(buttons=2), 1)
                self.assertEqual(d.kind, 'attention', '继续按钮不唯一时不放行')
                a = attempt(choice)
                d = a.decide(contact(fields=('family_name', 'given_name', 'phone')), 1)
                self.assertEqual(d.kind, 'attention', '证件后四位没回读一致时不放行')


if __name__ == '__main__':
    unittest.main()
