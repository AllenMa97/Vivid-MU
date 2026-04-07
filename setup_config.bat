@echo off
echo VividMU 项目配置设置向导
echo.

REM 检查是否已存在 user_config.txt
if exist user_config.txt (
    echo user_config.txt 已存在，跳过创建。
) else (
    echo 正在创建 user_config.txt 配置文件...
    REM 复制模板文件
    copy config_template.txt user_config.txt
    if errorlevel 1 (
        echo 错误：无法创建 user_config.txt
        goto :end
    )
    echo user_config.txt 已创建成功！
)

echo.
echo 请按照以下步骤完成配置：
echo 1. 打开 user_config.txt 文件
echo 2. 将 ALIYUN_API_KEY=YOUR_API_KEY_HERE 替换为您的实际API密钥
echo 3. 根据需要修改其他配置参数
echo.
echo 配置完成后，您可以运行主程序开始使用 VividMU。

:end
pause