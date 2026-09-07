.PHONY: run test web ui restart install-service
run:            ## run altd in the foreground on 127.0.0.1:8890 (ALTITUDE_HOST/PORT override)
	ALTITUDE_HOST=$${ALTITUDE_HOST:-127.0.0.1} bin/alt serve
test:           ## Python unit and integration tests (throwaway ALTITUDE_HOME)
	python3 -m unittest discover tests
web:            ## build the SPA into web/dist (frozen-lockfile pnpm install + build; needs node >= 22)
	cd web && export PATH="$$HOME/.nvm/versions/node/v24.14.0/bin:$$PATH" && pnpm install --frozen-lockfile && pnpm build
ui:             ## headless browser walkthroughs and route smoke at phone and desktop widths
	cd web && export PATH="$$HOME/.nvm/versions/node/v24.14.0/bin:$$PATH" && pnpm ui $(UI_ARGS)
restart:        ## safely rebuild the SPA, restart the user service, and verify the app
	python3 scripts/restart_altitude.py
install-service: ## user-level systemd unit (binds the WireGuard address) — run `make web` first so web/dist exists
	bin/alt install-git-guards
	mkdir -p ~/.config/systemd/user && cp systemd/altitude.service ~/.config/systemd/user/ && systemctl --user daemon-reload && systemctl --user enable --now altitude && systemctl --user status altitude --no-pager | head -5
	@echo "If ufw is active, open the port on the tunnel once:  sudo ufw allow in on wg0 to any port 8890 proto tcp"
