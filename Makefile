.PHONY: run test check check-python check-web web ui ui-ios audit-history installation-vm restart install-service
run:            ## run altd in the foreground on 127.0.0.1:8890 (ALTITUDE_HOST/PORT override)
	ALTITUDE_HOST=$${ALTITUDE_HOST:-127.0.0.1} bin/alt serve
test:           ## Python unit and integration tests (throwaway ALTITUDE_HOME)
	env -u ALTITUDE_ACTOR python3 -m unittest discover tests
check:          ## all deterministic checks, timed (install frozen dependencies and Chromium first)
	+$(MAKE) --no-print-directory -j2 -k check-python check-web
# GNU time writes its summary one character per write to stderr; its buffered -o stream writes it
# whole, so the two parallel branches cannot interleave their timings.
TIME = /usr/bin/time -p -a -o /dev/stderr
check-python:
	env -u ALTITUDE_ACTOR $(TIME) python3 tests/run_parallel.py --workers "$$(node -p 'Math.max(1, Math.floor(require("node:os").availableParallelism() / 2))')"
check-web:
	cd web && $(TIME) pnpm test
	cd web && $(TIME) pnpm build
	cd web && $(TIME) pnpm ui
web:            ## build the SPA into web/dist (supported Node and pnpm on PATH)
	cd web && pnpm install --frozen-lockfile && pnpm build
ui:             ## isolated headless browser walkthroughs at phone and desktop widths (build first)
	cd web && pnpm ui $(UI_ARGS)
ui-ios:         ## opt-in emulated iPhone walkthroughs in desktop WebKit, outside make check (build first)
	cd web && pnpm ui:ios $(UI_ARGS)
installation-vm: ## installation lifecycle, install.sh bootstrap and reboot in a throwaway KVM VM (RESULTS=dir [SOURCE=ref] [BASELINE=published tag [RECOVERY=1]])
	$(if $(RESULTS),,$(error Set RESULTS to a directory for the evidence))
	python3 scripts/installation_vm.py "$(RESULTS)" --source "$(or $(SOURCE),HEAD)" $(if $(BASELINE),--baseline-release "$(BASELINE)") $(if $(RECOVERY),--recovery)
audit-history:  ## scan reachable Git history for unpublishable material; exact matches stay in AUDIT_FINDINGS (AUDIT_SINCE=previous findings file, AUDIT_WORDS=private word list)
	@python3 scripts/audit_history.py --findings "$${AUDIT_FINDINGS:-$${ALTITUDE_HOME:-$$HOME/.altitude}/altitude/history-audit/$$(date -u +%Y%m%dT%H%M%SZ).json}" $(if $(AUDIT_SINCE),--since "$(AUDIT_SINCE)") $(if $(AUDIT_WORDS),--words "$(AUDIT_WORDS)")
restart:        ## safely rebuild the SPA, restart the user service, and verify the app
	python3 scripts/restart_altitude.py
install-service: ## user-level systemd unit, or a LaunchAgent on macOS (binds ALTITUDE_HOST from it) — run `make web` first so web/dist exists
	bin/alt install-git-guards
	if [ "$$(uname -s)" = Darwin ]; then python3 scripts/source_launch_agent.py; else \
	mkdir -p ~/.config/systemd/user && cp systemd/altitude.service ~/.config/systemd/user/ && systemctl --user daemon-reload && systemctl --user enable --now altitude && systemctl --user status altitude --no-pager | head -5; fi
	@echo "If ufw is active, open the port on your private interface once:  sudo ufw allow in on <interface> to any port 8890 proto tcp"
