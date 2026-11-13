# What counts as an FDE role

"Forward deployed engineer" is the clearest name for the job, but the same work (building and
deploying software inside a customer's environment, close to the customer, usually with LLMs
now) is posted under many titles. This list seeds the title matcher in
[`src/trawl/titles.py`](../src/trawl/titles.py). From M1 on, roles are classified by their
description instead, so a title missing here is not a job missed.

## Core: usually FDE work

| Title | Where it shows up |
|---|---|
| Forward Deployed Engineer / Forward Deployed Software Engineer | OpenAI, Anthropic, Palantir, Scale AI, many AI startups |
| Applied AI Engineer / AI Applied Engineer | Anthropic, AI startups |
| Applied AI Architect | Anthropic (the name its Greenhouse board uses) |
| Agent Engineer / Agent Deployment Engineer | Sierra, Decagon and other agent companies |
| Deployment Engineer / Deployment Strategist | Palantir (Deployment Strategist), AI startups |
| AI Solutions Engineer / AI Solutions Architect / Solutions Architect, GenAI | AI labs, cloud providers |

## Adjacent: sometimes FDE work, read the description

| Title | Notes |
|---|---|
| Applied Engineer | Varies from research to customer work |
| Solutions Engineer / Solutions Architect | FDE-like when it ships code, pre-sales when it doesn't |
| Implementation / Integration / Professional Services Engineer | Customer integrations, often less AI |
| Customer Engineer / Field Engineer | Google Cloud's name for its technical customer role |
| Customer Success Engineer (technical) | Post-sales, sometimes hands-on |
| Product Engineer (customer-facing) | Startups |
| AI Engineer / GenAI Engineer / AI Consultant | Singapore government, banks and consulting firms; varies a lot |
| AI Agent Developer | Newer title at agent startups |
| Sales Engineer / Pre-sales Engineer | Least hands-on of these; kept so nothing slips |

Founding engineer roles at customer-facing startups often do the same work, but there are too
many of them to match by title; M1's description classifier is meant to catch those.
