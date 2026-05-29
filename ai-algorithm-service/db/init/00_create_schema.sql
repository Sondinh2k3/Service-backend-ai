CREATE DATABASE IF NOT EXISTS statistic;
USE statistic;

CREATE TABLE IF NOT EXISTS traffic_flow (
  cross_id VARCHAR(64),
  total_vehicle INT,
  road_id VARCHAR(64),
  average_speed DOUBLE,
  created_date DATETIME,
  duration INT,
  queue_length DOUBLE,
  space_occupancy_rate DOUBLE
);
