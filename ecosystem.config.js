module.exports = {
  apps: [
    {
      name: "postulacion-bot",
      script: "/root/postulacion-bot/venv/bin/python3",
      args: "main.py",
      cwd: "/root/postulacion-bot",
      interpreter: "none",
      watch: false,
      autorestart: true,
      restart_delay: 5000,
      max_restarts: 10,
      env: {
        PYTHONUNBUFFERED: "1",
        PYTHONPATH: "/root/postulacion-bot"
      },
      error_file: "/root/postulacion-bot/logs/error.log",
      out_file: "/root/postulacion-bot/logs/output.log",
      log_date_format: "YYYY-MM-DD HH:mm:ss"
    }
  ]
}
