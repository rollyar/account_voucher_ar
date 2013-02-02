#This file is part of the account_voucher_ar module for Tryton.
#The COPYRIGHT file at the top level of this repository contains
#the full copyright notices and license terms.
from decimal import Decimal
from trytond.model import ModelSingleton, ModelView, ModelSQL, fields
from trytond.wizard import Wizard, StateView, StateTransition, Button
from trytond.transaction import Transaction
from trytond.pyson import Eval, In
from trytond.pool import Pool

__all__ = ['AccountVoucherSequence', 'AccountVoucherPayMode', 'AccountVoucher',
    'AccountVoucherLine', 'AccountVoucherLinePaymode', 'SelectInvoicesAsk',
    'SelectInvoices', ]

_STATES = {
    'readonly': In(Eval('state'), ['posted']),
}


class AccountVoucherSequence(ModelSingleton, ModelSQL, ModelView):
    'Account Voucher Sequence'
    __name__ = 'account.voucher.sequence'

    voucher_sequence = fields.Property(fields.Many2One('ir.sequence',
        'Voucher Sequence', required=True,
        domain=[('code', '=', 'account.voucher')]))


class AccountVoucherPayMode(ModelSQL, ModelView):
    'Account Voucher Pay Mode'
    __name__ = 'account.voucher.paymode'

    name = fields.Char('Name')
    account = fields.Many2One('account.account', 'Account')


class AccountVoucher(ModelSQL, ModelView):
    'Account Voucher'
    __name__ = 'account.voucher'
    _rec_name = 'number'

    number = fields.Char('Number', readonly=True, help="Voucher Number")
    party = fields.Many2One('party.party', 'Party', required=True,
        states=_STATES)
    voucher_type = fields.Selection([
        ('payment', 'Payment'),
        ('receipt', 'Receipt'),
        ], 'Type', select=True, required=True, states=_STATES)
    name = fields.Char('Memo', size=256, states=_STATES)
    pay_lines = fields.One2Many('account.voucher.line.paymode', 'voucher',
        'Pay Mode Lines', states=_STATES)
    date = fields.Date('Date', required=True, states=_STATES)
    journal = fields.Many2One('account.journal', 'Journal', required=True,
        states=_STATES)
    currency = fields.Many2One('currency.currency', 'Currency', required=True,
        states=_STATES)
    company = fields.Many2One('company.company', 'Company', required=True,
        states=_STATES)
    lines = fields.One2Many('account.voucher.line', 'voucher', 'Lines',
        states=_STATES)
    comment = fields.Text('Comment', states=_STATES)
    state = fields.Selection([
        ('draft', 'Draft'),
        ('posted', 'Posted'),
        ], 'State', select=True, readonly=True)
    amount = fields.Function(fields.Numeric('Payment', digits=(16, 2)),
        'amount_total')
    amount_pay = fields.Function(fields.Numeric('To Pay', digits=(16, 2)),
        'pay_amount')

    @classmethod
    def __setup__(cls):
        super(AccountVoucher, cls).__setup__()
        cls._error_messages.update({
            'partial_pay': 'Partial Payments are not allowed (yet)!',
        })
        cls._buttons.update({
                'post': {
                    'invisible': Eval('state') == 'posted',
                    },
                })

    def default_state(self):
        return 'draft'

    def default_currency(self):
        company_obj = Pool().get('company.company')
        if Transaction().context.get('company'):
            company = company_obj.browse(Transaction().context['company'])
            return company.currency.id

    def default_company(self):
        return Transaction().context.get('company')

    def default_date(self):
        date_obj = Pool().get('ir.date')
        return date_obj.today()

    def set_number(self, voucher_id):
        sequence_obj = Pool().get('ir.sequence')
        account_voucher_sequence_obj = Pool().get('account.voucher.sequence')

        sequence = account_voucher_sequence_obj.browse(1)
        self.write(voucher_id, {'number': sequence_obj.get_id(
            sequence.voucher_sequence.id)})

    def amount_total(self, ids, name):
        res = {}
        for voucher in self.browse(ids):
            res[voucher.id] = Decimal('0.0')
            if voucher.pay_lines:
                for line in voucher.pay_lines:
                    res[voucher.id] += line.pay_amount
        return res

    def pay_amount(self, ids, name):
        res = {}
        total = 0
        for voucher in self.browse(ids):
            if voucher.lines:
                for line in voucher.lines:
                    total += line.amount_original
            res[voucher.id] = total
        return res

    def prepare_moves(self, voucher_id):
        move_obj = Pool().get('account.move')
        period_obj = Pool().get('account.period')
        voucher = Pool().get('account.voucher').browse(voucher_id)
        new_moves = []
        if voucher.amount != voucher.amount_pay:
            self.raise_user_error('partial_pay')
        move_id = move_obj.create({
            'name': voucher.number,
            'period': period_obj.find(1, date=voucher.date),
            'journal': voucher.journal.id,
            'date': voucher.date,
        })

        #
        # Pay Modes
        #
        if voucher.pay_lines:
            for line in voucher.pay_lines:
                if voucher.voucher_type == 'receipt':
                    debit = line.pay_amount
                    credit = Decimal('0.0')
                else:
                    debit = Decimal('0.0')
                    credit = line.pay_amount

                new_moves.append({
                    'name': voucher.number,
                    'debit': debit,
                    'credit': credit,
                    'account': line.pay_mode.account.id,
                    'move': move_id,
                    'journal': voucher.journal.id,
                    'period': period_obj.find(1, date=voucher.date),
                    'party': voucher.party.id,
                })

        #
        # Voucher Lines
        #
        if voucher.lines:
            line_move_ids = []
            for line in voucher.lines:
                line_move_ids.append(line.move_line.id)
                if voucher.voucher_type == 'receipt':
                    debit = Decimal('0.00')
                    credit = Decimal(str(line.amount_original))
                else:
                    debit = Decimal(str(line.amount_original))
                    credit = Decimal('0.00')

                new_moves.append({
                    'name': voucher.number,
                    'debit': debit,
                    'credit': credit,
                    'account': line.account.id,
                    'move': move_id,
                    'journal': voucher.journal.id,
                    'period': period_obj.find(1, date=voucher.date),
                    'date': voucher.date,
                    'party': voucher.party.id,
                })
        return {
            'new_moves': new_moves,
            'invoice_moves': line_move_ids,
            'voucher_id': voucher.id,
            'move_id': move_id,
        }

    def create_moves(self, pay_moves, invoice_moves, voucher_id, move_id):
        move_obj = Pool().get('account.move')
        move_line_obj = Pool().get('account.move.line')

        created_moves = []
        to_reconcile = []
        for move_line in pay_moves:
            created_moves.append(move_line_obj.create(move_line))
        move_obj.write(move_id, {'state': 'posted'})
        for line in move_line_obj.browse(created_moves):
            if line.account.reconcile:
                to_reconcile.append(line.id)
        for invoice_line in invoice_moves:
            to_reconcile.append(invoice_line)
        move_line_obj.reconcile(to_reconcile)
        self.write(voucher_id, {'state': 'posted'})
        return True

    @ModelView.button
    def post(self, voucher_id):
        self.set_number(voucher_id[0])
        params = self.prepare_moves(voucher_id[0])
        self.create_moves(
                params.get('new_moves'),
                params.get('invoice_moves'),
                params.get('voucher_id'),
                params.get('move_id'),
            )
        return True


class AccountVoucherLine(ModelSQL, ModelView):
    'Account Voucher Line'
    __name__ = 'account.voucher.line'

    voucher = fields.Many2One('account.voucher', 'Voucher')
    name = fields.Char('Name')
    account = fields.Many2One('account.account', 'Account')
    amount = fields.Numeric('Amount', digits=(16, 2))
    line_type = fields.Selection([
        ('cr', 'Credit'),
        ('dr', 'Debit'),
        ], 'Type', select=True)
    move_line = fields.Many2One('account.move.line', 'Move Line')
    amount_original = fields.Numeric('Original Amount', digits=(16, 2))
    amount_unreconciled = fields.Numeric('Unreconciled amount', digits=(16, 2))


class AccountVoucherLinePaymode(ModelSQL, ModelView):
    'Account Voucher Line Pay Mode'
    __name__ = 'account.voucher.line.paymode'

    voucher = fields.Many2One('account.voucher', 'Voucher')
    pay_mode = fields.Many2One('account.voucher.paymode', 'Pay Mode',
        required=True, states=_STATES)
    pay_amount = fields.Numeric('Pay Amount', digits=(16, 2), required=True,
        states=_STATES)


class SelectInvoicesAsk(ModelView):
    'Select Invoices Ask'
    __name__ = 'account.voucher.select_invoices.ask'

    lines = fields.Many2Many('account.move.line', None, None,
        'Account Moves')


class SelectInvoices(Wizard):
    'Select Invoices'
    __name__ = 'account.voucher.select_invoices'

    start_state = 'search_lines'
    search_lines = StateTransition()
    select_lines = StateView('account.voucher.select_invoices.ask',
        'account_voucher_ar.view_search_invoices', [
            Button('Cancel', 'end', 'tryton-cancel'),
            Button('Add', 'add_lines', 'tryton-ok', default=True),
            ])
    add_lines = StateTransition()

    def transition_search_lines(self, session):
        voucher_obj = Pool().get('account.voucher')
        move_line = Pool().get('account.move.line')
        voucher = voucher_obj.browse(Transaction().context.get('active_id'))
        if voucher.voucher_type == 'receipt':
            account_types = ['receivable']
        else:
            account_types = ['payable']
        line_domain = [
            ('party', '=', voucher.party.id),
            ('account.kind', 'in', account_types),
            ('state', '=', 'valid'),
            ('reconciliation', '=', False),
        ]
        session.select_lines.lines = move_line.search(line_domain)
        return 'select_lines'

    def default_select_lines(self, session, fields):
        res = {}
        if session.select_lines.lines:
            res = {'lines': [l.id for l in session.select_lines.lines]}
        return res

    def transition_add_lines(self, session):
        voucher_line_obj = Pool().get('account.voucher.line')
        voucher = Pool().get('account.voucher').browse(
            Transaction().context.get('active_id'))
        move_line_obj = Pool().get('account.move.line')

        total_credit = 0
        total_debit = 0
        move_ids = session.select_lines.lines
        for line in move_ids:
            total_credit += line.credit
            total_debit += line.debit
            if line.credit:
                line_type = 'cr'
                amount = line.credit
            else:
                amount = line.debit
                line_type = 'dr'
            voucher_line_obj.create({
                'voucher': Transaction().context.get('active_id'),
                'name': line.name,
                'account': line.account.id,
                'amount_original': amount,
                'amount_unreconciled': amount,
                'line_type': line_type,
                'move_line': line.id,
            })
        voucher.write(Transaction().context.get('active_id'), {})
        return 'end'
