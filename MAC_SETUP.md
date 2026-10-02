# Mac Studio 遷移 / 設定指南 (jlaw_scanner)

本檔案供從 Windows notebook 搬到 macOS 後，喺 Mac Studio 上重建每日掃描器用。

## 1. Clone

```bash
cd ~/DEV            # 或你嘅工作目錄
git clone https://github.com/kmlau0807/jlaw_scanner.git
cd jlaw_scanner
```

## 2. 建立 Python venv 同裝依賴

```bash
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt
#   yfinance, pandas, numpy, apscheduler, flask, matplotlib
```

> 如果 `pip install yfinance` 抱怨 SSL / cert，先 `brew install python` 或用官方 python.org 安裝包。

## 3. 重建密碼檔（重要！GitHub 上冇呢啲）

`config.ini` 同相關 secret **唔會** push 上 GitHub（已 gitignore）。要喺 Mac 手動重建：

```bash
cp config.example.ini config.ini
```

然後編輯 `config.ini` 嘅 `[email]` 區，填入：
- `smtp_user` = `laukinming0807@gmail.com`
- `smtp_pass` = 你嘅 Gmail **App Password**（16 位，無空格）
- `smtp_to`  = `laukinming0807@gmail.com,achankk2@gmail.com,lkavana@gmail.com`

> 如果懷疑 App Password 外洩，去 Google Account → Security → App passwords 直接 revoke 再重發。

## 4. 手動測試一次

```bash
source venv/bin/activate
python sched_run.py          # 跑完整 scan + 寄 email（確認無 error、收得到信）
```

## 5. 設定每日自動執行（取代 Windows Task Scheduler）

`run_daily.bat` 係 Windows 專用，Mac 用 `run_daily.sh` + launchd：

```bash
chmod +x run_daily.sh

# 編輯 plist：將 USERNAME 同路徑换成你 Mac 嘅實際值
#   sed -i '' 's/USERNAME/你嘅username/' com.jlaw.scanner.plist
#   sed -i '' 's#/Users/USERNAME/DEV/jlaw_scanner#/實際/絕對/路徑#' com.jlaw.scanner.plist

mkdir -p ~/Library/LaunchAgents
cp com.jlaw.scanner.plist ~/Library/LaunchAgents/
launchctl load ~/Library/LaunchAgents/com.jlaw.scanner.plist
```

確認有排程：

```bash
launchctl list | grep jlaw
```

想即刻試跑一次（唔等 07:00）：

```bash
launchctl start com.jlaw.scanner
# 或者直接：
./run_daily.sh
tail -n 20 run_daily.log
```

> 唔想用 launchd 亦可以用 crontab：`0 7 * * * /絕對路徑/run_daily.sh`

## 6. Web UI（可選）

```bash
source venv/bin/activate
python -m flask --app web.app run --host=0.0.0.0 --port=5000
# 瀏覽器開 http://localhost:5000
```
