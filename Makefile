.PHONY: run test check web ui restart install-service
run:            ## run altd in the foreground on 127.0.0.1:8890 (ALTITUDE_HOST/PORT override)
	ALTITUDE_HOST=$${ALTITUDE_HOST:-127.0.0.1} bin/alt serve
test:           ## Python unit and integration tests (throwaway ALTITUDE_HOME)
	env -u ALTITUDE_ACTOR python3 -m unittest discover tests
check:          ## all deterministic checks, timed (install frozen dependencies and Chromium first)
	env -u ALTITUDE_ACTOR /usr/bin/time -p python3 -m unittest discover -v tests
	cd web && /usr/bin/time -p pnpm test
	cd web && /usr/bin/time -p pnpm build
	cd web && /usr/bin/time -p pnpm ui
web:            ## build the SPA into web/dist (supported Node and pnpm on PATH)
	cd web && pnpm install --frozen-lockfile && pnpm build
ui:             ## isolated headless browser walkthroughs at phone and desktop widths (build first)
	cd web && pnpm ui $(UI_ARGS)
restart:        ## safely rebuild the SPA, restart the user service, and verify the app
	python3 scripts/restart_altitude.py
install-service: ## user-level systemd unit (binds the WireGuard address) — run `make web` first so web/dist exists
	bin/alt install-git-guards
	mkdir -p ~/.config/systemd/user && cp systemd/altitude.service ~/.config/systemd/user/ && systemctl --user daemon-reload && systemctl --user enable --now altitude && systemctl --user status altitude --no-pager | head -5
	@echo "If ufw is active, open the port on the tunnel once:  sudo ufw allow in on wg0 to any port 8890 proto tcp"
