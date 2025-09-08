# ======= Location awareness (we are in tests/sannysoft) =======
REPO_ROOT := $(realpath $(CURDIR)/../..)
PY        ?= python

# ======= Config (override via env/CLI) =======
RUNS       ?= 10
BROWSERS   ?= firefox,chromium,webkit,camoufox
STEALTH    ?= stealth,base
MODES      ?= goto,render
SANNY_URL  ?= https://bot.sannysoft.com/
HEADLESS   ?= false
SLEEP_SEC  ?= 1.0
OUT_JSON   ?= $(CURDIR)/browser_antibot_sannysoft.json

# ======= Internal =======
export PYTHONPATH := $(REPO_ROOT)

.PHONY: help sannysoft-stats sannysoft-stats-firefox sannysoft-stats-all sannysoft-open

help:
	@echo "Targets:"
	@echo "  make sannysoft-stats            # collect stats with current vars"
	@echo "  make sannysoft-stats-firefox    # preset: only firefox"
	@echo "  make sannysoft-stats-all        # preset: all browsers"
	@echo "  make sannysoft-open             # pretty-print OUT_JSON"
	@echo ""
	@echo "Vars (override like RUNS=40 HEADLESS=true):"
	@echo "  RUNS=$(RUNS)"
	@echo "  BROWSERS=$(BROWSERS)"
	@echo "  STEALTH=$(STEALTH)"
	@echo "  MODES=$(MODES)"
	@echo "  SANNY_URL=$(SANNY_URL)"
	@echo "  HEADLESS=$(HEADLESS)"
	@echo "  SLEEP_SEC=$(SLEEP_SEC)"
	@echo "  OUT_JSON=$(OUT_JSON)"
	@echo "  PYTHONPATH=$(PYTHONPATH)"

sannysoft-stats:
	@echo "→ Collecting stats"
	@echo "   runs=$(RUNS) browsers=$(BROWSERS) stealth=$(STEALTH) modes=$(MODES)"
	@echo "   headless=$(HEADLESS) url=$(SANNY_URL)"
	@RUNS='$(RUNS)' \
	BROWSERS='$(BROWSERS)' \
	STEALTH_OPS='$(STEALTH)' \
	MODES='$(MODES)' \
	SANNYSOFT_URL='$(SANNY_URL)' \
	HEADLESS='$(HEADLESS)' \
	SLEEP_SEC='$(SLEEP_SEC)' \
	OUT_JSON='$(OUT_JSON)' \
	$(PY) $(CURDIR)/collect_antibot_stats.py
	@echo "✓ Saved: $(OUT_JSON)"

sannysoft-stats-firefox:
	@$(MAKE) -s sannysoft-stats BROWSERS=firefox

sannysoft-stats-all:
	@$(MAKE) -s sannysoft-stats BROWSERS='firefox,chromium,webkit,camoufox'

sannysoft-open:
	@test -f '$(OUT_JSON)' && (jq '.' '$(OUT_JSON)' || cat '$(OUT_JSON)') || echo "no $(OUT_JSON)"
