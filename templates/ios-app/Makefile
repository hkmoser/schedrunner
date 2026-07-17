# iOS-Shell — turn-key entrypoints. Run these on the Mac mini.
.DEFAULT_GOAL := help
SHELL := /bin/bash
LOG_DIR := $(HOME)/Library/Logs/__APP_NAME_LOWER__

# Release channel: stable (default) or next. `make deploy CHANNEL=next` (run from the next
# worktree) brings up the parallel stack on :8081/:8100/:8443. Every Deploy script derives its
# labels/ports/paths from DASHBOARD_CHANNEL, so stable is unchanged when CHANNEL is unset.
# Default from the checkout's pin (Deploy/.channel, written by next-setup in the next worktree),
# so commands run there are channel-correct even without CHANNEL=next. Explicit CHANNEL= wins.
CHANNEL ?= $(shell cat Deploy/.channel 2>/dev/null || echo stable)
export DASHBOARD_CHANNEL := $(CHANNEL)
# Label/log suffix for the channel ("" for stable, "-next" for next) — mirrors Deploy/lib.sh.
CH_SFX := $(if $(filter-out stable,$(CHANNEL)),-$(CHANNEL),)

.PHONY: help deploy doctor update update-web update-server logs restart docs schemas schemas-refresh dev-server dev-web build-web test-web ios-generate ios-register ios-deploy ios-autodeploy ios-status ios-keychain secrets-setup deploy-status auto-deploy auto-deploy-status auto-deploy-timer auto-deploy-timer-off next-setup channel-status

help: ## Show this help
	@grep -E '^[a-zA-Z_-]+:.*?## .*$$' $(MAKEFILE_LIST) | \
	  awk 'BEGIN{FS=":.*?## "}{printf "  \033[1m%-12s\033[0m %s\n",$$1,$$2}'

deploy: ## Full idempotent bring-up + healthcheck (prints the install URL)
	@bash Deploy/up.sh

doctor: ## Preflight + healthcheck; prints exactly what's missing
	@bash Deploy/doctor.sh

update: ## Rebuild web + sidecar, mirror docs, reload (rebuilds the Vapor server ONLY if it changed) — no sudo
	@bash Deploy/setup_server.sh && bash Deploy/setup_bq.sh && bash Deploy/sync_docs.sh

update-web: ## Fast: rebuild only the PWA and reload — never compiles the Vapor server
	@WEB_ONLY=1 bash Deploy/setup_server.sh

update-server: ## Force a full Vapor server rebuild (use after server-code changes if auto-detect missed it)
	@FORCE_SERVER_BUILD=1 bash Deploy/setup_server.sh && bash Deploy/setup_bq.sh && bash Deploy/sync_docs.sh

docs: ## Mirror Google Drive /Private Markdown locally so Docs is always available (run on a timer too)
	@bash Deploy/sync_docs.sh

bq: ## (Re)deploy only the BigQuery sidecar (after changing BQ_QUERY/BQ_DATASET in .env)
	@bash Deploy/setup_bq.sh

secrets-setup: ## Enable Google Secret Manager for the in-app Settings page (idempotent)
	@bash Deploy/setup_secrets.sh

schemas: ## Dump BigQuery + Drive-export schemas to docs/SCHEMAS.md (commit it so Claude sees real shapes)
	@bash Deploy/dump_schemas.sh

schemas-refresh: ## Regenerate + push docs/SCHEMAS.md on branch schemas/auto (unattended; safe for a daily schedule)
	@bash Deploy/refresh_schemas.sh

power: ## One-time: stop the Mac from sleeping (prompts for your password)
	@sudo pmset -a sleep 0 && sudo systemsetup -setcomputersleep Never && echo "sleep disabled"

restart: ## Restart the running server service (honors CHANNEL=)
	@launchctl kickstart -k gui/$$(id -u)/__BUNDLE_ID__.server$(CH_SFX) && echo "restarted ($(CHANNEL))"

deploy-status: ## Is the running server my latest code? Compares /healthz build to local HEAD + checks the sidecar
	@bash Deploy/deploy_status.sh

auto-deploy: ## Post-pull deploy hook — make the running server match the checked-out code (what the external deploy service runs after `git pull`)
	@bash Deploy/auto_deploy.sh; cat build/.auto-deploy-status 2>/dev/null || true

auto-deploy-status: ## Show the last post-pull-deploy result + recent log
	@echo "last run:      $$(cat build/.auto-deploy-status 2>/dev/null || echo 'never run')"
	@echo "log tail:      tail -n 30 build/auto-deploy.log"

auto-deploy-timer: ## Install a fallback launchd timer that runs .auto-deploy on a schedule (self-heals a stale server)
	@bash Deploy/setup_auto_deploy.sh

auto-deploy-timer-off: ## Remove the fallback auto-deploy timer
	@bash Deploy/setup_auto_deploy.sh off

next-setup: ## Create the 'next' channel worktree (branch next) beside this repo — then: cd there && make deploy CHANNEL=next
	@bash Deploy/setup_channel.sh

channel-status: ## Show both stacks side by side (stable :8080 vs next :8081)
	@printf "stable :8080  "; curl -fsS --max-time 3 http://127.0.0.1:8080/healthz || echo "(down)"
	@printf "next   :8081  "; curl -fsS --max-time 3 http://127.0.0.1:8081/healthz || echo "(down / not deployed)"

logs: ## Tail the server logs (honors CHANNEL=)
	@tail -n 80 -f "$(LOG_DIR)/server$(CH_SFX).out.log" "$(LOG_DIR)/server$(CH_SFX).err.log"

dev-server: ## Run the server locally on :8080 (stub data if no keys)
	@cd Server && swift run

dev-web: ## Run the web dev server (proxies to :8080, falls back to sample)
	@cd Web && npm install && npm run dev

build-web: ## Build the production web bundle
	@cd Web && npm ci && npm run build

test-web: ## Web checks: typecheck + schema contract + render smoke
	@cd Web && npm install && npm test

ios-generate: ## Native iOS: generate __APP_NAME__.xcodeproj from project.yml (then open in Xcode)
	@command -v xcodegen >/dev/null || { echo "install xcodegen: brew install xcodegen"; exit 1; }
	@bash Deploy/gen_ios_config.sh
	@xcodegen generate && echo "open __APP_NAME__.xcodeproj"

ios-register: ## Native iOS: detect + record the iPhone UDID for device install
	@bash Deploy/register_device.sh

ios-deploy: ## Native iOS: turn-key build + OTA install (auto Team ID/host; just be signed into Xcode)
	@bash Deploy/build_and_install_ota.sh

ios-autodeploy: ## Native iOS: background rebuild IF native sources changed (used by `make deploy`; opt-in via DEPLOY_IOS=1)
	@bash Deploy/ios_autodeploy.sh

ios-keychain: ## Native iOS: ONE-TIME codesign keychain setup so builds never prompt for your password
	@bash Deploy/setup_codesign.sh

ios-status: ## Native iOS: auto-build on? last/current build + is a background build IN PROGRESS or STUCK?
	@bash Deploy/ios_status.sh
