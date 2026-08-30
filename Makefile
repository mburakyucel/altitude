.PHONY: run test web install-service
run:            ## run altd in the foreground on 127.0.0.1:8890 (ALTITUDE_HOST/PORT override)
	ALTITUDE_HOST=$${ALTITUDE_HOST:-127.0.0.1} bin/alt serve
test:           ## lifecycle self-test (throwaway ALTITUDE_HOME) + rules unit tests
	python3 -m unittest discover tests
web:            ## build the SPA into web/dist (frozen-lockfile pnpm install + build; needs node >= 22)
	cd web && export PATH="$$HOME/.nvm/versions/node/v24.14.0/bin:$$PATH" && pnpm install --frozen-lockfile && pnpm build
install-service: ## user-level systemd unit (binds the WireGuard address) — run `make web` first so web/dist exists
	mkdir -p ~/.config/systemd/user && cp systemd/altitude.service ~/.config/systemd/user/ && systemctl --user daemon-reload && systemctl --user enable --now altitude && systemctl --user status altitude --no-pager | head -5
	@echo "If ufw is active, open the port on the tunnel once:  sudo ufw allow in on wg0 to any port 8890 proto tcp"
