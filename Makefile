.PHONY: run test check check-python check-web web ui ui-shell ui-ios ui-simulator audit-history installation-vm installation-mac installation-macos-vm browser-sandbox restart install-service
run:            ## run altd in the foreground on 127.0.0.1:8890 (ALTITUDE_HOST/PORT override)
	ALTITUDE_HOST=$${ALTITUDE_HOST:-127.0.0.1} bin/alt serve
test:           ## Python unit and integration tests (throwaway ALTITUDE_HOME)
	env -u ALTITUDE_ACTOR python3 -m unittest discover tests
check:          ## all deterministic checks, timed (install frozen dependencies and Chromium first)
	+$(MAKE) --no-print-directory -j2 -k check-python check-web
# One timing write keeps parallel summaries together on both supported hosts.
TIME = python3 "$(CURDIR)/scripts/time_command.py"
check-python:
	env -u ALTITUDE_ACTOR $(TIME) python3 tests/run_parallel.py --workers "$$(node -p 'Math.max(1, Math.floor(require("node:os").availableParallelism() / 2))')"
check-web:
	cd web && $(TIME) pnpm test
	cd web && $(TIME) pnpm build
	cd web && $(TIME) pnpm ui
	cd web && $(TIME) pnpm ui:shell
web:            ## build the SPA into web/dist (supported Node and pnpm on PATH)
	cd web && pnpm install --frozen-lockfile && pnpm build
ui:             ## isolated headless browser walkthroughs at phone and desktop widths (build first; CAPTURE=1 with UI_ARGS=spec keeps GIFs in web/ui-artifacts/captures)
	$(if $(CAPTURE),$(if $(UI_ARGS),,$(error CAPTURE=1 records only the journeys UI_ARGS selects)))
	cd web && $(if $(CAPTURE),ALTITUDE_UI_CAPTURE=ui-artifacts/captures) pnpm ui $(UI_ARGS)
ui-shell:       ## fictional project/draft/clipboard recovery lane in Chromium headless shell (build first)
	cd web && pnpm ui:shell $(UI_ARGS)
ui-validate:    ## disposable candidate fictional browser checks; invoke with alt task validate -- make ui-validate [UI_ARGS=spec [CAPTURE=1]]
	python3 scripts/validate_ui.py $(if $(CAPTURE),--capture) $(UI_ARGS)
ui-ios:         ## opt-in emulated iPhone walkthroughs in desktop WebKit, outside make check (build first; CAPTURE=1 with UI_ARGS=spec keeps GIFs in web/ui-artifacts/ios/captures)
	$(if $(CAPTURE),$(if $(UI_ARGS),,$(error CAPTURE=1 records only the journeys UI_ARGS selects)))
	cd web && $(if $(CAPTURE),ALTITUDE_UI_CAPTURE=ui-artifacts/ios/captures) pnpm ui:ios $(UI_ARGS)
ui-simulator:   ## opt-in phone walkthrough in iOS Safari on a disposable Simulator iPhone (macOS, [RESULTS=dir] [CAPTURE=1]); inside a task, through the validation runner
ifdef ALTITUDE_TASK
	alt task validate --simulator $(if $(CAPTURE),--capture) -- sh -c 'make web && make ui-simulator'
else
	python3 scripts/ios_simulator.py $(RESULTS)
endif
installation-vm: ## installation lifecycle, install.sh bootstrap and reboot in a throwaway KVM VM (RESULTS=dir [SOURCE=ref] [BASELINE=published tag [RECOVERY=1 | PUBLIC=1]] [CAPTURE=1]); inside a task, through the validation runner
ifdef ALTITUDE_TASK
	alt task validate --kvm -- make installation-vm RESULTS=/results SOURCE="$(or $(SOURCE),HEAD)" $(if $(BASELINE),BASELINE="$(BASELINE)") $(if $(RECOVERY),RECOVERY=1) $(if $(PUBLIC),PUBLIC=1) $(if $(CAPTURE),CAPTURE=1)
else
	$(if $(RESULTS),,$(error Set RESULTS to a directory for the evidence))
	python3 scripts/installation_vm.py "$(RESULTS)" --source "$(or $(SOURCE),HEAD)" $(if $(BASELINE),--baseline-release "$(BASELINE)") $(if $(RECOVERY),--recovery) $(if $(PUBLIC),--public) $(if $(CAPTURE),--capture)
endif
installation-mac: ## installation lifecycle on this Mac under a throwaway HOME and LaunchAgent label: install.sh, update detection, alt update, the Update button, failed-update recovery, uninstall (RESULTS=dir [SOURCE=ref]); outside the worker sandbox, inside a task with alt task run
	$(if $(RESULTS),,$(error Set RESULTS to a directory for the evidence))
	python3 scripts/installation_mac.py "$(RESULTS)" --source "$(or $(SOURCE),HEAD)"
installation-macos-vm: ## The public install.sh in throwaway offline macOS guests on this Apple silicon Mac: refusals, lifecycle, login after a restart (RESULTS=dir [SOURCE=ref]); inside a task, through `alt task run` under an operator grant
	$(if $(RESULTS),,$(error Set RESULTS to a directory for the evidence))
	python3.12 scripts/installation_macos_vm.py image
	python3.12 scripts/installation_macos_vm.py run "$(RESULTS)" --source "$(or $(SOURCE),HEAD)"
container-vm: ## actual container image/launcher lifecycle and authorization checks in a disposable Ubuntu KVM VM (RESULTS=dir)
ifdef ALTITUDE_TASK
	alt task validate --kvm -- make container-vm RESULTS=/results/container-vm
else
	$(if $(RESULTS),,$(error Set RESULTS to a new directory for the evidence))
	timeout 1800s python3 scripts/container_vm.py "$(RESULTS)"
endif
browser-sandbox: ## Playwright's Chromium with its own sandbox against a local fictional page, recording the protections it keeps (RESULT=file); inside a task, through the validation runner
ifdef ALTITUDE_TASK
	alt task validate -- make browser-sandbox RESULT=/results/browser-sandbox.json
else
	$(if $(RESULT),,$(error Set RESULT to a file for the evidence))
	cd web && pnpm install --frozen-lockfile --silent
	node scripts/browser_sandbox.mjs "$(RESULT)"
endif
audit-history:  ## scan reachable Git history for unpublishable material; exact matches stay in AUDIT_FINDINGS (AUDIT_SINCE=previous findings file, AUDIT_WORDS=private word list)
	@python3 scripts/audit_history.py --findings "$${AUDIT_FINDINGS:-$${ALTITUDE_HOME:-$$HOME/.altitude}/altitude/history-audit/$$(date -u +%Y%m%dT%H%M%SZ).json}" $(if $(AUDIT_SINCE),--since "$(AUDIT_SINCE)") $(if $(AUDIT_WORDS),--words "$(AUDIT_WORDS)")
restart:        ## safely rebuild the SPA, restart the user service, and verify the app
	python3 scripts/restart_altitude.py
install-service: ## user-level systemd unit (binds ALTITUDE_HOST from the unit), or a LaunchAgent on macOS (ALTITUDE_HOST from the environment, default 127.0.0.1) — run `make web` first so web/dist exists
	bin/alt install-git-guards
	if [ "$$(uname -s)" = Darwin ]; then python3 scripts/source_launch_agent.py; else \
	mkdir -p ~/.config/systemd/user && cp systemd/altitude.service ~/.config/systemd/user/ && systemctl --user daemon-reload && systemctl --user enable --now altitude && systemctl --user status altitude --no-pager | head -5; fi
	@echo "If ufw is active, open the port on your private interface once:  sudo ufw allow in on <interface> to any port 8890 proto tcp"
