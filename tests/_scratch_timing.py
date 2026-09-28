import sys
import time

from modules.medicine_search import search_medicine

names = sys.argv[1:] or ["Dolo 650", "Metformin", "Augmentin 625"]

for name in names:
    t0 = time.time()
    r = search_medicine(name, "en")
    print("=== %-24s matched=%-5s conf=%-8s sources=%-30s %.1fs" % (
        name, r["matched"], r["confidence"], ",".join(r["sources"]), time.time() - t0))
    print("    display : %s" % r["display_name"])
    print("    generic : %s | std: %s | form: %s" % (r["generic_name"], r["standard_name"], r["form"]))
    print("    uses    : %s" % r["uses"][:90])
    print("    warn    : %s" % r["warnings"][:90])
    print("    desc    : %s" % r["description"][:90])
