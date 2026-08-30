.PHONY: run test install-service
run:            ## run altd in the foreground on 127.0.0.1:8890 (ALTITUDE_HOST/PORT override)
	ALTITUDE_HOST=$${ALTITUDE_HOST:-127.0.0.1} bin/alt serve
test:           ## lifecycle self-test against a throwaway ALTITUDE_HOME
	python3 tests/test_lifecycle.py
	python3 -m unittest tests.test_rules
install-service: ## user-level systemd unit (binds the WireGuard address)
	mkdir -p ~/.config/systemd/user && cp systemd/altitude.service ~/.config/systemd/user/ && systemctl --user daemon-reload && systemctl --user enable --now altitude && systemctl --user status altitude --no-pager | head -5
	@echo "If ufw is active, open the port on the tunnel once:  sudo ufw allow in on wg0 to any port 8890 proto tcp"
