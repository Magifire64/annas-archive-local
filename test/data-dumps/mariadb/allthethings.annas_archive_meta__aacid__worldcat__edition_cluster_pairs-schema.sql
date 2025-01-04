/*!40101 SET NAMES binary*/;
/*!40014 SET FOREIGN_KEY_CHECKS=0*/;
/*!40101 SET SQL_MODE='NO_AUTO_VALUE_ON_ZERO,ERROR_FOR_DIVISION_BY_ZERO,NO_AUTO_CREATE_USER,NO_ENGINE_SUBSTITUTION'*/;
/*!40103 SET TIME_ZONE='+00:00' */;
CREATE TABLE `annas_archive_meta__aacid__worldcat__edition_cluster_pairs` (
  `query_oclc_id` bigint(20) NOT NULL,
  `record_oclc_id` bigint(20) NOT NULL,
  PRIMARY KEY (`query_oclc_id`,`record_oclc_id`),
  KEY `record_oclc_id` (`record_oclc_id`,`query_oclc_id`)
) ENGINE=MyISAM DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_bin;
