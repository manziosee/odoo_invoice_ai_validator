# AI Invoice Payment Validator

> An Odoo 17 module that uses **Groq AI** to read a proof of payment document, find the matching unpaid invoice, and register the payment — all in a few clicks.

---

## What It Does

In many businesses, accountants receive proof of payment (bank slips, transfer screenshots, PDF receipts) from clients like UNICEF, NGOs, or government agencies. Manually hunting through dozens of unpaid invoices to find the right one wastes time and introduces errors.

**This module automates that entire workflow:**

1. **Upload** a proof of payment (image or PDF)
2. **Groq AI** reads the document and extracts: payer name, amount, currency, date, payment reference, and bank details
3. The system **scans all unpaid customer invoices** and scores each one against the extracted data
4. The best match is shown to the accountant for **review**
5. One click **registers the payment** in Odoo and marks the invoice as paid

### Example

> UNICEF sends a bank transfer slip as a PDF. The accountant uploads it. The AI reads it, finds invoice `INV/2026/00042` for the matching amount, and the accountant clicks **Validate Payment** — done in under 30 seconds.

---

## Tech Stack

<table>
  <tr>
    <td align="center" width="120">
      <img src="https://www.odoo.com/web/image/website.library_image_08" width="48"/><br/>
      <b>Odoo 17</b><br/>
      <sub>ERP Platform</sub>
    </td>
    <td align="center" width="120">
      <img src="https://upload.wikimedia.org/wikipedia/commons/c/c3/Python-logo-notext.svg" width="48"/><br/>
      <b>Python 3.10</b><br/>
      <sub>Backend Logic</sub>
    </td>
    <td align="center" width="120">
      <img src="https://upload.wikimedia.org/wikipedia/commons/6/61/HTML5_logo_and_wordmark.svg" width="48"/><br/>
      <b>XML / QWeb</b><br/>
      <sub>Views & Templates</sub>
    </td>
    <td align="center" width="120">
      <img src="https://upload.wikimedia.org/wikipedia/commons/2/29/Postgresql_elephant.svg" width="48"/><br/>
      <b>PostgreSQL 16</b><br/>
      <sub>Database</sub>
    </td>
    <td align="center" width="120">
      <img src="https://upload.wikimedia.org/wikipedia/commons/4/4e/Docker_%28container_engine%29_logo.svg" width="64"/><br/>
      <b>Docker</b><br/>
      <sub>Deployment</sub>
    </td>
  </tr>
  <tr>
    <td align="center" width="120">
      <img src="https://groq.com/favicon.ico" width="48"/><br/>
      <b>Groq API</b><br/>
      <sub>AI Inference</sub>
    </td>
    <td align="center" width="120">
      <img src="https://upload.wikimedia.org/wikipedia/commons/a/a7/Camponotus_flavomarginatus_ant.jpg" width="48"/><br/>
      <b>Llama 3.2 Vision</b><br/>
      <sub>AI Model (Meta)</sub>
    </td>
    <td align="center" width="120">
      <img src="https://upload.wikimedia.org/wikipedia/commons/6/6a/JavaScript-logo.png" width="48"/><br/>
      <b>JavaScript</b><br/>
      <sub>Frontend (OWL)</sub>
    </td>
    <td align="center" width="120">
      <img src="https://upload.wikimedia.org/wikipedia/commons/d/d5/CSS3_logo_and_wordmark.svg" width="48"/><br/>
      <b>CSS / SCSS</b><br/>
      <sub>Styling</sub>
    </td>
    <td align="center" width="120">
      <img src="https://upload.wikimedia.org/wikipedia/commons/2/29/Postgresql_elephant.svg" width="48"/><br/>
      <b>pdfminer / PyMuPDF</b><br/>
      <sub>PDF Parsing</sub>
    </td>
  </tr>
</table>

---

## Module Structure

```
odoo_invoice_ai_validator/
├── models/
│   ├── payment_proof.py          # Core model — state machine & workflow
│   └── res_config_settings.py   # Groq API key & model configuration
├── services/
│   ├── groq_service.py           # Groq API integration (vision + text)
│   └── invoice_matcher.py        # Scoring engine — matches proof to invoice
├── wizard/
│   ├── validate_payment_wizard.py       # Confirmation step before posting
│   └── validate_payment_wizard_views.xml
├── views/
│   ├── menu.xml                         # Accounting → AI Payment Validator
│   ├── payment_proof_views.xml          # List, form, search views
│   └── res_config_settings_views.xml    # Settings page
├── data/
│   └── sequence.xml              # PAY-PROOF-0001, 0002…
└── security/
    └── ir.model.access.csv       # Access rights
```

---

## Workflow States

```
Draft  ──►  Analyzing…  ──►  Match Found  ──►  Validated
                │                  │
                ▼                  ▼
              Error            (manual fix,
                               then retry)
```

| State | Meaning |
|---|---|
| **Draft** | Proof uploaded, not yet analyzed |
| **Analyzing** | Groq AI is processing the document |
| **Match Found** | An invoice was matched — awaiting accountant confirmation |
| **Validated** | Payment registered in Odoo, invoice marked paid |
| **Error** | AI failed or no invoice matched — details shown inline |

---

## Invoice Matching Score

The matching engine scores each unpaid invoice on 4 criteria:

| Criteria | Max Points |
|---|---|
| Payment reference found in invoice name / ref / narration | 50 pts |
| Extracted amount within tolerance of invoice balance | 30 pts |
| Payer name matches client name (fuzzy) | 15 pts |
| Payment date within 30 days of invoice date | 5 pts |

A match must score **≥ 25 points** to be proposed. The highest-scoring invoice wins. The accountant can override the match manually before validating.

---

## Installation

### 1. Python dependencies (inside the Odoo container)

```bash
docker-compose exec odoo17 pip install groq pdfminer.six PyMuPDF
```

### 2. Install the Odoo module

Go to **Apps** → search **AI Invoice Payment Validator** → **Install**

### 3. Configure the Groq API key

Go to **Accounting → Configuration → Settings → AI Payment Validator**

- Paste your Groq API key (free at [console.groq.com](https://console.groq.com))
- Choose the AI model (Llama 3.2 11B Vision recommended)
- Set the amount match tolerance (default: 2%)

---

## Supported File Formats

| Format | Processing Method |
|---|---|
| PNG, JPG, WEBP | Sent directly to Groq vision model as base64 |
| PDF (digital/text) | Text extracted via `pdfminer.six` → sent as text prompt |
| PDF (scanned/image) | Text extracted via `PyMuPDF` → sent as text prompt |
| TXT, CSV | Read as plain text → sent as text prompt |

---

## Access Rights

| Role | Create | Read | Write | Delete |
|---|---|---|---|---|
| Accounting User | ✅ | ✅ | ✅ | ❌ |
| Accounting Manager | ✅ | ✅ | ✅ | ✅ |

---

## Configuration Reference

| Setting | Key | Default |
|---|---|---|
| Groq API Key | `odoo_invoice_ai_validator.groq_api_key` | *(required)* |
| Groq Model | `odoo_invoice_ai_validator.groq_model` | `llama-3.2-11b-vision-preview` |
| Amount Tolerance % | `odoo_invoice_ai_validator.match_amount_tolerance` | `2.0` |

---

## Developer

| | |
|---|---|
| **Developer** | Manzi Osee |
| **Email** | manziosee3@gmail.com |
| **GitHub** | [github.com/manziosee](https://github.com/manziosee) |
| **Module Version** | 17.0.1.0.0 |
| **License** | LGPL-3 |

---

## License

This module is released under the [GNU Lesser General Public License v3 (LGPL-3)](https://www.gnu.org/licenses/lgpl-3.0.html).
