module.exports = {
  apps: [
    {
      name: "katty-jobs",
      script: "/root/katty-jobs/venv/bin/python3",
      args: "main.py",
      cwd: "/root/katty-jobs",
      interpreter: "none",
      watch: false,
      autorestart: true,
      restart_delay: 5000,
      max_restarts: 10,
      env: {
        PYTHONUNBUFFERED: "1",
        PYTHONPATH: "/root/katty-jobs"
      },
      error_file: "/root/katty-jobs/logs/error.log",
      out_file: "/root/katty-jobs/logs/output.log",
      log_date_format: "YYYY-MM-DD HH:mm:ss"
    }
  ]
}
