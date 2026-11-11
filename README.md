# U60 Pro 语音禁用位解锁工具

从一台 ZTE U60 Pro 的实际调试流程中提取的独立工具。只处理 NV 5280（`NV_DISABLE_CM_CALL_TYPE_I`）最低位：清除语音呼叫禁用位，保留其余 127 字节和第一字节的其他位。无需安装 OpenU60，也不修改 APN、IMEI、频段、运营商配置或固件分区。

## 已验证范围

原调试设备上，该位清除后，QMI 语音拨号成功，AT+CLCC 曾确认振铃和已接通；重启后该位仍保持清除。**这里发布的重构工具尚未在第二台新设备上验证，不保证所有硬件批次、固件或运营商适用。**工具只接受 ARM64 Linux、root ADB、SDXPINN SKU、预期诊断应答以及已观察到的 NV 前 8 字节 `0102000000000000` / `0002000000000000`。这些是必要检查，不构成所有固件兼容性的证明。

解锁不等于运营商 IMS 注册成功，也不等于已经实现手机双向通话音频。RTP/HAL 音频、iOS App、SIP、CallKit 不在此仓库。原调试中的最近一次拨号发生设备连接丢失，原因尚未确定；本工具不包含自动拨号。

## 准备

- 自有 U60 Pro、可靠 USB 数据线及稳定供电。
- Python 3.10 或更新版本，仅使用标准库。
- 从 [Google 官方下载 Android SDK Platform-Tools](https://developer.android.com/tools/releases/platform-tools)，将 `adb` 加入 PATH；Windows 可用全路径 `--adb "C:\platform-tools\adb.exe"`。
- 手机卡需要开通运营商 VoLTE；不要在通话、固件升级期间运行写入操作。
- 将仓库下载或克隆到本机，在仓库目录运行以下命令。不得使用别人的 NV 备份。

## 新设备操作步骤

1. 用 USB 连接路由器，电脑接入它的局域网。若尚未开启 ADB：

   ```sh
   python unlock.py enable-adb
   ```

   按提示输入本机路由器网页管理密码，密码不会写入文件。该步骤使用原厂局域网 HTTP 管理接口，仅应在可信的直连网络执行。默认网关为 `192.168.0.1`，不同网关可用 `python unlock.py --router 192.168.1.1 enable-adb`。固件若不支持该接口，工具停止；不绕过管理员密码。调试模式切换可能导致 USB 重连，必要时重新插拔。ADB 的开启状态不承诺重启后保持。

2. 检查 ADB 及只读兼容性：

   ```sh
   adb devices
   python unlock.py inspect
   ```

   多设备连接时，每个命令都加 `--serial YOUR_DEVICE_SERIAL`，放在子命令前。未知 SKU、非 root ADB、未知协议停止，不要修改脚本强行绕过检查。

3. 备份并解锁：

   ```sh
   python unlock.py unlock
   ```

   工具先将原始 128 字节写入 `private-backups/` 并刷入磁盘，再写 NV，最后逐字节回读验证。反复执行不会继续改变已经清除的位。保存**第一次解锁前的备份**；后续备份可能已是解锁状态。备份含设备绑定指纹和原始 NV，只保留在本地，禁止上传。

4. 手动重启路由器。恢复 ADB 连接后，用第 3 步打印的备份路径核验：

   ```sh
   python unlock.py verify private-backups/YOUR_BACKUP.json
   ```

   同时检查设备绑定、全部 128 字节以及 boot ID 已改变。只有全部通过才报告重启后持久化成功。同一次启动中运行 verify 会拒绝宣称持久化。若重启后 ADB 关闭，先重新执行 `enable-adb`。

## 为什么不安装开机注入脚本

原设备的 NV 写入本身已跨重启保留，因此无需定时或开机重复写基带。若你的固件重启后恢复该位，`verify` 会失败；这类固件不在已验证范围，不自动注入或反复写入。OTA 升级后需要重新评估兼容性。

## 恢复

```sh
python unlock.py restore private-backups/YOUR_ORIGINAL_BACKUP.json
```

恢复前会再备份当前值，并回读验证恢复结果。工具要求备份匹配当前 ADB 设备标识、型号、SKU 和固件信息的哈希，拒绝跨设备或可检测到的固件信息变化后的恢复。校验和用于发现意外损坏，不是防篡改签名。恢复后可手动重启，再用 `inspect` 检查禁用位。

写入期间断连或回读不一致时，保留备份，恢复连接后先 inspect，不应持续重试写入。只备份 NV 5280，不声称替代完整基带备份。

## 文件及验证

- `unlock.py`：原厂调试接口、设备检查、NV 备份/解锁/恢复/重启后核验。
- `diag.py`：通过 `qrtr-lookup` 自动发现当前启动中的 `DIAG service (MODEM:CMD)` 节点和端口，再运行时生成最小 ARM64 Linux QRTR 程序；上传到独立临时目录，结束后尝试清理。不分发厂商二进制。B29 等固件或不同启动中端口可能变化，不再假定 node 3 / port 24。
- `test_unlock.py`：纯离线测试，不接触路由器。

```sh
python -m unittest -v
```

发布内容不含账号密码、电话、SIM 标识、ADB 序列号、个人路径、设备日志、基带备份或厂商固件。Git 使用文件白名单，临时二进制和私有备份默认排除。

背景参考：[OpenU60](https://github.com/jesther-ai/open-u60-pro)、[MU5250 VoIP/SLIC 分析](https://github.com/amenekowo/mu5250_tweaking/blob/main/docs/voip-slic.md)。本仓库并未打包这些项目或厂商库，也不代表厂商提供的通用解锁支持。
