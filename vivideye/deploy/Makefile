# ============================================================================
# VividEye 部署工具（PC 端 · Linux）
#
# 用法（在本 deploy/ 目录下执行，或 make -C deploy <目标>）：
#   make deploy   一键部署到手机（装 APK + 推代码 + 触发手机端 bootstrap）
#   make doctor   部署后体检（Termux 进程 / 8666 Web / 8080 摄像头）
#   make reset    恢复出厂/三清（危险！脚本内有二次确认）
#
# 透传参数示例： make deploy ARGS="--yes"   make reset ARGS="--fastboot"
# ============================================================================

ARGS ?=

.PHONY: help deploy doctor reset

help:
	@echo "VividEye 部署工具（PC 端）"
	@echo "  make deploy   一键部署到手机（装APK + 推代码 + 触发bootstrap）"
	@echo "  make doctor   部署后体检（Termux / 8666 Web / 8080 摄像头）"
	@echo "  make reset    恢复出厂/三清（危险！需二次确认）"
	@echo "  可透传参数: ARGS=\"--yes\" 或 ARGS=\"--fastboot\"（reset）"

deploy:
	bash scripts/deploy.sh $(ARGS)

doctor:
	bash scripts/doctor.sh $(ARGS)

reset:
	bash scripts/flash/reset-emui.sh $(ARGS)
