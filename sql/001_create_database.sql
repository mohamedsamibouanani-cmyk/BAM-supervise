CREATE DATABASE IF NOT EXISTS bam_supervise CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci;
CREATE USER IF NOT EXISTS 'bam_user'@'localhost' IDENTIFIED BY 'bam_password';
GRANT ALL PRIVILEGES ON bam_supervise.* TO 'bam_user'@'localhost';
FLUSH PRIVILEGES;
