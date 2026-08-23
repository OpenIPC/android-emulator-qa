# Android emulator QA harness
SHELL := /bin/bash
ROOT  := $(shell pwd)
SDK   := $(ROOT)/sdk
ADB   := $(SDK)/platform-tools/adb
EMU   := $(SDK)/emulator/emulator
export ANDROID_SDK_ROOT := $(SDK)
export ANDROID_HOME := $(SDK)

CAMERA_IP   ?= 192.168.1.100
APK         ?= $(HOME)/tinyCam-v18.1.2_build_68770.apk
RUN         ?= runs/$(shell date +%Y%m%d-%H%M%S)-tinycam-onvif

.PHONY: bootstrap boot boot-cold install kill relay mvp analyze clean-runs

bootstrap:            ## Install JDK+SDK+emulator+image+AVD (idempotent)
	./bootstrap.sh

boot:                 ## Boot emulator headless with KVM (from snapshot)
	nohup $(EMU) -avd qa -no-window -gpu swiftshader_indirect -no-audio \
	  -no-boot-anim -accel on >/tmp/emu.log 2>&1 &
	@echo "booting; run 'make wait'"

boot-cold:            ## Boot fresh (no snapshot)
	nohup $(EMU) -avd qa -no-window -gpu swiftshader_indirect -no-audio \
	  -no-boot-anim -no-snapshot -accel on >/tmp/emu.log 2>&1 &

wait:                 ## Block until boot_completed
	@for i in $$(seq 1 60); do \
	  [ "$$($(ADB) -s emulator-5554 shell getprop sys.boot_completed 2>/dev/null | tr -d '\r')" = "1" ] \
	    && { echo booted; exit 0; }; sleep 3; done; echo "timeout" >&2; exit 1

install:              ## Install the APK ($(APK))
	$(ADB) install -r -g "$(APK)"

kill:                 ## Stop the emulator
	-$(ADB) -s emulator-5554 emu kill

# Full MVP: relay + drive tinyCam ONVIF + analyze. Emulator must be booted.
# NOTE: capture via the relay, not emulator -tcpdump (see CLAUDE.md).
mvp:
	@mkdir -p $(RUN)/relay
	@echo "RUN=$(RUN)"
	python3 harness/relay.py --outdir $(RUN)/relay \
	  --map 8080:$(CAMERA_IP):80 --map 8554:$(CAMERA_IP):554 & echo $$! > $(RUN)/relay.pid
	sleep 1
	python3 flows/tinycam_onvif.py --host 10.0.2.2 --onvif-port 8080 \
	  --rtsp-port 8554 --user root --password 123456 --run-dir $(RUN) || true
	@kill $$(cat $(RUN)/relay.pid) 2>/dev/null || true
	python3 harness/analyze_relay.py $(RUN)/relay

analyze:              ## Analyze the newest relay capture: make analyze RUN=runs/<ts>...
	python3 harness/analyze_relay.py $(RUN)/relay

clean-runs:
	rm -rf runs/*

help:
	@grep -E '^[a-zA-Z_-]+:.*?## .*$$' $(MAKEFILE_LIST) | \
	  awk 'BEGIN{FS=":.*?## "}{printf "  \033[36m%-12s\033[0m %s\n",$$1,$$2}'
