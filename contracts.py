"""Render a loan's signed contract as a self-contained PDF."""

from datetime import date, datetime
from decimal import Decimal, ROUND_HALF_UP
from html import escape
from io import BytesIO

from num2words import num2words
from reportlab.lib import colors
from reportlab.lib.enums import TA_CENTER
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import cm
from reportlab.platypus import (
    Image, KeepTogether, LongTable, Paragraph, SimpleDocTemplate, Spacer, Table,
    TableStyle,
)


MONTHS = ("enero", "febrero", "marzo", "abril", "mayo", "junio", "julio", "agosto", "septiembre", "octubre", "noviembre", "diciembre")
FREQUENCIES = {"weekly": "semanales", "fortnightly": "quincenales", "monthly": "mensuales"}


def pesos(value):
    return "$" + f"{value:,.0f}".replace(",", ".") + " COP"


def words(value):
    return f"{num2words(value, lang='es').upper()} {'PESO' if value == 1 else 'PESOS'} M/CTE"


def long_date(value):
    value = date.fromisoformat(value) if isinstance(value, str) else value
    return f"{value.day} de {MONTHS[value.month - 1]} de {value.year}"


def build_contract_pdf(loan, client, settings, installments, debtor_signature, lender_signature, signed_at):
    output = BytesIO()
    document = SimpleDocTemplate(
        output, pagesize=A4, rightMargin=1.8 * cm, leftMargin=1.8 * cm,
        topMargin=1.7 * cm, bottomMargin=1.8 * cm,
        title=f"Contrato de préstamo #{loan['id']} - PrestaYa",
        author="PrestaYa",
    )
    styles = getSampleStyleSheet()
    normal = ParagraphStyle("ContractBody", parent=styles["Normal"], fontName="Helvetica", fontSize=9.2, leading=13, spaceAfter=6)
    heading = ParagraphStyle("ContractHeading", parent=normal, fontName="Helvetica-Bold", spaceBefore=7, spaceAfter=4)
    title = ParagraphStyle("ContractTitle", parent=heading, alignment=TA_CENTER, fontSize=12.5, leading=16, spaceAfter=12)
    small = ParagraphStyle("ContractSmall", parent=normal, fontSize=8, leading=11)

    def paragraph(value, style=normal):
        return Paragraph(value, style)

    def field(value):
        return escape(str(value))

    debtor_name = f"{client['first_name']} {client['last_name']}"
    rate = f"{loan['monthly_rate_bps'] / 100:g}"
    per_month = int((Decimal(loan["principal"] * loan["monthly_rate_bps"]) / Decimal(10000)).quantize(Decimal("1"), rounding=ROUND_HALF_UP))
    signed_date = datetime.fromisoformat(signed_at).date()
    story = [paragraph("CONTRATO DE PRÉSTAMO DE DINERO<br/>(MUTUO CON INTERÉS)", title)]
    story.append(paragraph(
        f"Entre los suscritos: <b>EL PRESTAMISTA</b>, {field(settings['lender_name'])}, identificado(a) con "
        f"Cédula de Ciudadanía No. {field(settings['lender_identification'])}, expedida en "
        f"{field(settings['lender_id_issued_in'])}, domiciliado(a) en {field(settings['lender_city'])}; "
        f"y <b>EL DEUDOR</b>, {field(debtor_name)}, identificado(a) con Cédula de Ciudadanía No. "
        f"{field(client['identification'])}, expedida en {field(client['id_issued_in'])}, "
        f"domiciliado(a) en {field(client['city'])}, acuerdan celebrar este contrato bajo las siguientes cláusulas:"
    ))
    story.extend([
        paragraph("PRIMERA. OBJETO Y MONTO DEL PRÉSTAMO", heading),
        paragraph(
            f"EL PRESTAMISTA entrega en calidad de préstamo a EL DEUDOR, quien declara recibir a satisfacción, "
            f"la suma de <b>{words(loan['principal'])} ({pesos(loan['principal'])})</b>."
        ),
        paragraph("SEGUNDA. INTERESES", heading),
        paragraph(
            f"El capital genera un interés corriente pactado de <b>{rate}% mensual</b>. "
            f"El interés calculado para cada mes es de aproximadamente {pesos(per_month)}. "
            f"Durante el plazo de {loan['term_months']} "
            f"{'mes' if loan['term_months'] == 1 else 'meses'}, el interés total es "
            f"<b>{words(loan['interest_amount'])} ({pesos(loan['interest_amount'])})</b>."
        ),
    ])
    summary = Table([
        ["Capital prestado", pesos(loan["principal"])],
        ["Intereses totales", pesos(loan["interest_amount"])],
        ["Total a pagar", pesos(loan["total_amount"])],
    ], colWidths=[10 * cm, 6 * cm])
    summary.setStyle(TableStyle([
        ("BOX", (0, 0), (-1, -1), .5, colors.HexColor("#CEDCEB")),
        ("INNERGRID", (0, 0), (-1, -1), .35, colors.HexColor("#E3EAF2")),
        ("BACKGROUND", (0, -1), (-1, -1), colors.HexColor("#EAF3FF")),
        ("FONTNAME", (0, -1), (-1, -1), "Helvetica-Bold"),
        ("FONTSIZE", (0, 0), (-1, -1), 9),
        ("TOPPADDING", (0, 0), (-1, -1), 5),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 5),
    ]))
    story.extend([
        summary,
        paragraph("TERCERA. FORMA Y PLAZO DE PAGO", heading),
        paragraph(
            f"EL DEUDOR se obliga a pagar {pesos(loan['total_amount'])} en "
            f"<b>{len(installments)} cuotas {FREQUENCIES[loan['frequency']]}</b>, "
            "por los importes y en las fechas siguientes:"
        ),
    ])
    rows = [["Cuota", "Fecha de pago", "Importe"]]
    rows.extend([str(item["number"]), long_date(item["due_date"]), pesos(item["amount"])] for item in installments)
    schedule = LongTable(rows, colWidths=[2.2 * cm, 8.2 * cm, 5.6 * cm], repeatRows=1)
    schedule.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#145FB4")),
        ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
        ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
        ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.white, colors.HexColor("#F3F7FC")]),
        ("FONTSIZE", (0, 0), (-1, -1), 8.5),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
        ("TOPPADDING", (0, 0), (-1, -1), 4),
    ]))
    story.extend([
        schedule,
        Spacer(1, 8),
        paragraph(f"<b>Lugar y forma de pago:</b> {field(settings['payment_instructions'])}"),
        paragraph("CUARTA. INCUMPLIMIENTO Y MORA", heading),
        paragraph(
            "Si EL DEUDOR no paga alguna cuota en la fecha convenida, incurrirá en mora sin necesidad "
            "de reconvención o requerimiento judicial. EL PRESTAMISTA podrá exigir la cancelación "
            "inmediata del saldo pendiente, conforme a la ley aplicable."
        ),
        paragraph("QUINTA. ACEPTACIÓN Y FIRMA", heading),
        paragraph(
            f"Leído y aceptado por las partes, se firma en {field(settings['lender_city'])} el "
            f"{long_date(signed_date)}. Ambas partes conservan una copia del documento firmado."
        ),
    ])
    signature_rows = [
        [Image(BytesIO(lender_signature), width=5.7 * cm, height=1.5 * cm),
         Image(BytesIO(debtor_signature), width=5.7 * cm, height=1.5 * cm)],
        [paragraph("<b>EL PRESTAMISTA</b>", small), paragraph("<b>EL DEUDOR</b>", small)],
        [paragraph(field(settings["lender_name"]), small), paragraph(field(debtor_name), small)],
        [paragraph("C.C. " + field(settings["lender_identification"]), small),
         paragraph("C.C. " + field(client["identification"]), small)],
        [paragraph("Tel. " + field(settings["lender_phone"]), small),
         paragraph("Tel. " + field(client["phone"]), small)],
    ]
    signatures = Table(signature_rows, colWidths=[8 * cm, 8 * cm])
    signatures.setStyle(TableStyle([("VALIGN", (0, 0), (-1, -1), "BOTTOM"), ("TOPPADDING", (0, 0), (-1, 0), 10)]))
    story.append(KeepTogether([Spacer(1, 10), signatures]))

    def footer(canvas, doc):
        canvas.saveState()
        canvas.setFont("Helvetica", 8)
        canvas.setFillColor(colors.HexColor("#70849C"))
        canvas.drawString(1.8 * cm, 1 * cm, f"PrestaYa · Contrato de préstamo #{loan['id']}")
        canvas.drawRightString(A4[0] - 1.8 * cm, 1 * cm, f"Página {doc.page}")
        canvas.restoreState()

    document.build(story, onFirstPage=footer, onLaterPages=footer)
    return output.getvalue()
