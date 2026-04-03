import asyncio
import aiohttp
import re
import sys
import time
import json

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.9",
}

SKIP_DOMAINS = {
    "bankofamerica.com", "salesforce.com", "venmo.com", "paypal.com",
    "braintreegateway.com", "stripe.com", "shopify.dev", "shopify.engineering",
    "scribd.com", "npr.org", "simplywall.st", "tracxn.com", "hindenburgresearch.com",
    "gsb.stanford.edu", "richmondfed.org", "newyorkfed.org", "aarp.org",
    "springer.com", "coinmarketcap.com", "statista.com", "nzherald.co.nz",
    "fortune.com", "amazon.com", "amazon.se", "amazonaws.cn", "macrumors.com",
    "zdnet.com", "lifehacker.com", "coursera.org", "indiamart.com",
    "economictimes.com", "timesofindia.indiatimes.com", "cbinsights.com",
    "pib.gov.in", "bsi.bund.de", "visa.com", "americanexpress.com",
    "citizensbank.com", "icici.bank.in", "hdfcsec.com", "paytmmoney.com",
    "archive.org", "github.com", "getpostman.com", "rapidapi.com",
    "drupal.org", "zapier.com", "pabbly.com", "chatgpt.com",
    "microsoft.com", "google.com", "apple.com", "logitech.com",
    "cyberpowerpc.com", "michaels.com", "footlocker.com", "ikea.com",
    "zappos.com", "ulta.com", "lenovo.com", "bhg.com.au",
    "revolve.com", "playboy.com", "hottopic.com", "instacart.ca",
    "dollskill.com", "alexanderwang.com", "pcrichard.com",
    "stopandshop.com", "woshub.com",
}


def should_skip(url):
    from urllib.parse import urlparse
    try:
        host = urlparse(url).hostname or ""
        for skip in SKIP_DOMAINS:
            if host == skip or host.endswith("." + skip):
                return True
    except:
        return False
    return False


async def scan_url(session, url, sem):
    async with sem:
        if should_skip(url):
            return None
        try:
            async with session.get(
                url, headers=HEADERS,
                timeout=aiohttp.ClientTimeout(total=8),
                allow_redirects=True, ssl=False
            ) as r:
                if r.status >= 400:
                    return None
                html = await r.text()
                if len(html) < 200:
                    return None

                result = {"url": url, "flags": [], "pk": None, "score": 0}

                pks = re.findall(r'pk_live_[A-Za-z0-9]{20,}', html)
                if pks:
                    result["pk"] = pks[0][:50]
                    result["flags"].append("STRIPE_PK")
                    result["score"] += 3

                if re.search(r'data-token="[^"]+"', html):
                    result["flags"].append("DATA_TOKEN")
                    result["score"] += 2

                if "wpforms" in html.lower():
                    result["flags"].append("WPFORMS")
                    result["score"] += 2

                if re.search(r'woocommerce|wc-checkout|wc_checkout|wc-ajax', html, re.I):
                    result["flags"].append("WOOCOMMERCE")
                    result["score"] += 2

                if re.search(r'stripe[_-]?element|card-element|payment-element|#card-element', html, re.I):
                    result["flags"].append("STRIPE_ELEM")
                    result["score"] += 2

                if re.search(r'braintree|brain_tree', html, re.I):
                    bt_tokens = re.findall(r'sandbox_[a-z0-9]+_[a-z0-9]+|production_[a-z0-9]+_[a-z0-9]+', html)
                    if bt_tokens:
                        result["flags"].append("BT_TOKEN")
                        result["score"] += 3
                    else:
                        result["flags"].append("BRAINTREE")
                        result["score"] += 1

                if re.search(r'give-form|givewp|give_payment', html, re.I):
                    result["flags"].append("GIVEWP")
                    result["score"] += 2

                if re.search(r'donation|donate', html, re.I):
                    result["flags"].append("DONATION")
                    result["score"] += 1

                if re.search(r'three_d_secure|3ds|3d.secure|threedsecure', html, re.I):
                    result["flags"].append("3DS_REF")
                    result["score"] += 1

                if re.search(r'checkout.*form|payment.*form|billing.*form', html, re.I):
                    result["flags"].append("CHECKOUT_FORM")
                    result["score"] += 1

                if re.search(r'stripe\.createPaymentMethod|stripe\.confirmCardPayment|stripe\.createToken', html, re.I):
                    result["flags"].append("STRIPE_JS_CALL")
                    result["score"] += 3

                if re.search(r'wp-admin/admin-ajax\.php', html):
                    result["flags"].append("WP_AJAX")
                    result["score"] += 1

                cko_pks = re.findall(r'pk_[a-z0-9_-]{20,}', html)
                cko_pks = [p for p in cko_pks if not p.startswith("pk_live_")]
                if cko_pks:
                    result["flags"].append("CKO_PK")
                    result["score"] += 2

                if result["score"] >= 3:
                    return result
                return None
        except:
            return None


async def main(filepath, concurrency=50):
    with open(filepath) as f:
        urls = [line.strip() for line in f if line.strip() and line.strip().startswith("http")]

    print(f"Loaded {len(urls)} URLs, scanning with {concurrency} concurrent connections...\n")

    sem = asyncio.Semaphore(concurrency)
    connector = aiohttp.TCPConnector(limit=concurrency, ssl=False)

    results = []
    done = 0
    total = len(urls)
    start = time.time()

    async with aiohttp.ClientSession(connector=connector) as session:
        batch_size = 200
        for batch_start in range(0, total, batch_size):
            batch = urls[batch_start:batch_start + batch_size]
            tasks = [scan_url(session, url, sem) for url in batch]
            batch_results = await asyncio.gather(*tasks)
            for r in batch_results:
                if r:
                    results.append(r)
            done += len(batch)
            elapsed = time.time() - start
            print(f"  Progress: {done}/{total} ({done*100//total}%) | Found: {len(results)} | {elapsed:.0f}s", flush=True)

    results.sort(key=lambda x: x["score"], reverse=True)

    print(f"\n{'='*70}")
    print(f"  CHECKER MATERIAL SCAN RESULTS")
    print(f"  Scanned: {total} | Hits: {len(results)}")
    print(f"{'='*70}\n")

    for r in results:
        pk_str = r["pk"] or "none"
        flags = " ".join(r["flags"])
        print(f"  [{r['score']:2d}] {r['url']}")
        print(f"       PK: {pk_str}")
        print(f"       Flags: {flags}")
        print()

    with open("scan_results.json", "w") as f:
        json.dump(results, f, indent=2)
    print(f"Full results saved to scan_results.json")


if __name__ == "__main__":
    filepath = sys.argv[1] if len(sys.argv) > 1 else "../attached_assets/vps_rdp_1775053254024.txt"
    asyncio.run(main(filepath))
