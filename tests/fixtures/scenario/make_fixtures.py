"""Generate the synthetic Scenario-Based Assessment fixture PDFs.

Run:  python tests/fixtures/scenario/make_fixtures.py
Writes (next to this file):
  learning_supply_chain.pdf      - PDF 1, learning content (5 pages, headings)
  learning_supply_chain_v2.pdf   - revised learning content (replacement test)
  sample_questions.pdf           - PDF 2, instructor sample questions
  reference_answers.pdf          - PDF 3, general conceptual reference answers
  scanned_image_only.pdf         - image-only page (no text layer)
These are SYNTHETIC teaching materials written for tests; swap in the client's real
documents for acceptance testing.
"""
from __future__ import annotations

import io
import os

from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import getSampleStyleSheet
from reportlab.platypus import PageBreak, Paragraph, SimpleDocTemplate, Spacer

HERE = os.path.dirname(os.path.abspath(__file__))

LEARNING = [
    ("1. Supply Chain Management and Business Strategy", [
        "A supply chain is the network of organisations - suppliers, manufacturers, distributors, retailers and "
        "customers - linked by flows of materials, information and money. Supply chain management (SCM) is the "
        "coordination of these flows so that the right product reaches the right customer at the right time and cost.",
        "A firm's supply chain strategy must fit its competitive strategy. A cost-leadership strategy needs an "
        "efficient (lean) supply chain with high utilisation, low inventory and economies of scale. A "
        "differentiation or innovation strategy needs a responsive supply chain with buffer capacity, flexible "
        "suppliers and faster lead times. Strategic fit means the uncertainty of customer demand is matched by the "
        "responsiveness of the chain; a mismatch raises cost or loses sales.",
    ]),
    ("2. Supply Chain Integration", [
        "Supply chain integration is the degree to which a firm strategically collaborates with its partners and "
        "manages intra- and inter-organisational processes as one system. Internal integration links purchasing, "
        "production, logistics and sales inside the firm. External integration links the firm with suppliers "
        "(supplier integration) and customers (customer integration).",
        "Integration mechanisms include shared planning (sales and operations planning, S&OP), joint product "
        "development, vendor-managed inventory (VMI), electronic data interchange and long-term contracts. "
        "Benefits include lower inventory, shorter lead times, fewer stock-outs and faster response to change. "
        "Barriers include lack of trust, incompatible information systems, conflicting performance measures and "
        "fear that a partner will misuse shared information.",
    ]),
    ("3. Supply Chain Coordination", [
        "Coordination exists when all stages of the chain take actions that together increase total supply chain "
        "profit. Lack of coordination occurs when each stage optimises its local objective, or when information is "
        "distorted as it moves along the chain.",
        "Obstacles to coordination include incentive obstacles (for example sales-force incentives based on "
        "sell-in to retailers), information-processing obstacles (forecasting from orders rather than from real "
        "customer demand), operational obstacles (large order batches and long replenishment lead times), pricing "
        "obstacles (quantity discounts and trade promotions that encourage forward buying) and behavioural "
        "obstacles (each stage reacting only to its local situation, lack of trust, blaming partners).",
        "Managerial levers that restore coordination are: aligning goals and incentives across stages, improving "
        "the visibility and accuracy of information, improving operational performance by reducing lead times and "
        "batch sizes, designing pricing strategies that stabilise orders (for example everyday low pricing instead "
        "of promotions), and building strategic partnerships and trust.",
    ]),
    ("4. The Bullwhip Effect", [
        "The bullwhip effect is the increase in the variability of orders as they move up the supply chain from "
        "retailer to wholesaler to manufacturer to supplier, even when end-customer demand is stable. Each stage "
        "sees a more volatile order stream than the stage below it.",
        "Four classic causes are: demand-signal processing (each stage forecasts from the orders it receives and "
        "adds safety stock), order batching (periodic large orders for transport or ordering economies), price "
        "fluctuations (promotions cause customers to buy ahead and then stop), and rationing and shortage gaming "
        "(when supply is short, customers inflate orders and cancel later).",
        "Consequences include excess inventory, higher manufacturing and transport cost, longer lead times, poor "
        "service levels and damaged relationships. Remedies include sharing point-of-sale (POS) data with "
        "upstream partners, collaborative planning, forecasting and replenishment (CPFR), VMI, smaller and more "
        "frequent orders, reduced lead times, everyday low pricing and allocating scarce supply on past sales "
        "rather than on current orders.",
    ]),
    ("5. Information Sharing and Operational Efficiency", [
        "Information sharing means partners exchange demand, inventory, capacity, production-plan and "
        "promotion data. Accurate, timely shared information reduces uncertainty, so each stage needs less safety "
        "stock and can plan capacity better. It is the most effective single counter-measure to the bullwhip effect, "
        "but it only helps when partners trust each other and act on the information.",
        "Operational efficiency is producing and delivering the required output with the least use of resources: "
        "inventory, capacity, transport and labour. Typical measures are inventory turns, order fill rate, "
        "on-time delivery, cash-to-cash cycle time and total landed cost. Efficiency gains must be balanced with "
        "responsiveness: cutting all buffers lowers cost but makes the chain fragile when demand or supply changes.",
        "Firms improve efficiency through integration and coordination: shared forecasts reduce over-production, "
        "smaller batches cut inventory, cross-docking reduces handling, and joint performance measures stop one "
        "partner from pushing cost onto another.",
    ]),
]

LEARNING_V2_EXTRA = ("6. Supply Chain Resilience (revised edition)", [
    "Resilience is the ability of a supply chain to prepare for, respond to and recover from disruptions. "
    "Resilience levers include multi-sourcing, strategic buffer stock for critical items, supplier risk "
    "monitoring and flexible logistics. Resilience and efficiency must be balanced deliberately.",
])

SAMPLES = [
    "Q1. A regional supermarket chain notices that its suppliers' orders swing far more than its own weekly "
    "sales. Explain what is happening, identify two likely causes in this situation, and recommend actions the "
    "supermarket and its suppliers should take together.",
    "Q2. A low-cost furniture retailer has adopted a highly responsive supply chain with many small, fast "
    "deliveries. Evaluate whether its supply chain fits its competitive strategy and justify your recommendation.",
    "Q3. Two partners in an electronics supply chain refuse to share forecast data because each fears the other "
    "will gain bargaining power. Analyse the consequences for coordination and propose a way to build the trust "
    "needed for information sharing.",
    "Q4. A beverage manufacturer runs frequent price promotions to distributors. Discuss how this practice affects "
    "order variability and operational efficiency, and suggest an alternative pricing approach.",
]

REFERENCES = [
    ("Understanding the bullwhip effect", [
        "A good answer recognises that order variability amplifies upstream even if consumer demand is stable. It "
        "links the observed symptoms to specific causes - forecasting from orders, batching, promotions and "
        "shortage gaming - instead of only naming the effect. Strong answers explain the mechanism: each stage adds "
        "safety stock to an already distorted signal, so distortion compounds.",
        "Appropriate remedies must match the cause: POS data sharing or CPFR for demand-signal distortion; smaller, "
        "more frequent orders or consolidated transport for batching; everyday low pricing for promotion-driven "
        "swings; allocation on past sales for shortage gaming. Answers that list remedies without linking them to "
        "causes show only partial understanding.",
    ]),
    ("Coordination and incentives", [
        "Answers about coordination should show that local optimisation by each stage can reduce total chain "
        "profit. Credit is given for identifying the type of obstacle (incentive, information, operational, pricing, "
        "behavioural) and proposing a lever that addresses it, such as shared performance measures, revenue sharing "
        "or joint planning. Claiming that coordination simply means 'ordering more inventory' is incorrect.",
    ]),
    ("Information sharing and trust", [
        "Information sharing reduces uncertainty and safety stock, but it is not automatic: partners must trust that "
        "data will not be used against them. Good answers propose trust-building mechanisms such as confidentiality "
        "agreements, starting with limited data, gain-sharing contracts, long-term relationships and neutral "
        "third-party platforms. Saying that sharing information always removes the bullwhip effect completely is an "
        "overstatement.",
    ]),
    ("Strategic fit, efficiency and responsiveness", [
        "A strong answer evaluates fit by comparing the demand uncertainty and competitive strategy with the "
        "responsiveness of the chain. A cost-leader needs efficiency (lean inventory, full loads, scale); an "
        "innovator needs responsiveness. Recommending responsiveness for a pure cost-leader without justification "
        "shows a misunderstanding of strategic fit. Excellent answers note the trade-off: efficiency without any "
        "buffer makes the chain fragile.",
    ]),
]


def _doc(path: str, sections, title: str, page_break_each=True):
    styles = getSampleStyleSheet()
    story = [Paragraph(title, styles["Title"]), Spacer(1, 12)]
    for i, (heading, paras) in enumerate(sections):
        story.append(Paragraph(heading, styles["Heading2"]))
        for p in paras:
            story.append(Paragraph(p, styles["BodyText"]))
            story.append(Spacer(1, 8))
        if page_break_each and i < len(sections) - 1:
            story.append(PageBreak())
    SimpleDocTemplate(path, pagesize=A4).build(story)


def _scanned(path: str):
    import fitz

    pix_doc = fitz.open()
    page = pix_doc.new_page()
    img = fitz.Pixmap(fitz.csRGB, fitz.IRect(0, 0, 400, 200), False)
    img.clear_with(200)
    page.insert_image(fitz.Rect(50, 50, 450, 250), pixmap=img)
    pix_doc.save(path)


def main():
    _doc(os.path.join(HERE, "learning_supply_chain.pdf"), LEARNING, "Operations Management - Managing the Supply Chain")
    _doc(os.path.join(HERE, "learning_supply_chain_v2.pdf"), LEARNING + [LEARNING_V2_EXTRA],
         "Operations Management - Managing the Supply Chain (Revised)")
    _doc(os.path.join(HERE, "sample_questions.pdf"), [("Sample Assessment Questions", SAMPLES)],
         "Supply Chain Management - Sample Questions", page_break_each=False)
    _doc(os.path.join(HERE, "reference_answers.pdf"), REFERENCES, "General Reference Answers and Marking Guidance")
    _scanned(os.path.join(HERE, "scanned_image_only.pdf"))
    print("fixtures written to", HERE)


if __name__ == "__main__":
    main()
