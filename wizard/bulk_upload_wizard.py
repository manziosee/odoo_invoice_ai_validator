import base64
from odoo import models, fields, api, _
from odoo.exceptions import UserError


class BulkUploadWizard(models.TransientModel):
    _name = 'bulk.upload.wizard'
    _description = 'Bulk Upload Proofs of Payment'

    proof_type = fields.Selection([
        ('customer_payment', 'Customer Payment'),
        ('vendor_bill', 'Vendor Bill'),
    ], string='Type', default='customer_payment', required=True)

    partner_id = fields.Many2one('res.partner', string='Client (optional)',
        help='If set, all uploaded proofs will be linked to this client.')

    process_async = fields.Boolean(string='Process in Background', default=True,
        help='Queue all proofs for background AI analysis.')

    # Multiple file attachments stored as ir.attachment then read
    attachment_ids = fields.Many2many(
        'ir.attachment', string='Proof Files',
        help='Select one or more proof of payment files (PDF, PNG, JPG).',
    )

    result_summary = fields.Text(string='Upload Summary', readonly=True)

    def action_upload(self):
        self.ensure_one()
        if not self.attachment_ids:
            raise UserError(_('Please attach at least one file.'))

        created = []
        for att in self.attachment_ids:
            if not att.datas:
                continue
            proof = self.env['payment.proof'].create({
                'proof_type': self.proof_type,
                'partner_id': self.partner_id.id if self.partner_id else False,
                'proof_file': att.datas,
                'proof_filename': att.name,
                'process_async': self.process_async,
                'state': 'queued' if self.process_async else 'draft',
            })
            created.append(proof)

        if not created:
            raise UserError(_('No valid files were processed.'))

        summary_lines = [f'{p.name} — {p.proof_filename}' for p in created]
        self.result_summary = (
            f'{len(created)} proof(s) created'
            + (' and queued for processing.' if self.process_async else '.')
            + '\n' + '\n'.join(summary_lines)
        )

        # Return list view of created proofs
        return {
            'type': 'ir.actions.act_window',
            'name': _('Uploaded Proofs'),
            'res_model': 'payment.proof',
            'view_mode': 'tree,form',
            'domain': [('id', 'in', [p.id for p in created])],
        }
