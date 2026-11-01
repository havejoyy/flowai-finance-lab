# 行业指数服务器缺失的中间证书

来源：https://cacerts.digicert.com/GeoTrustG2TLSCNRSA4096SHA2562022CA1.crt
2026-10-31 通过验证 HTTPS 下载 DER，转换为 PEM。服务器 *.swsresearch.com 的叶证书仅返回自身，缺少此中间证书。
构建时用 certifi 的已有根验证此中间证书，再与原根集合连接。没有信任服务器叶证书，没有启用 PARTIAL_CHAIN、关闭主机名或证书校验；运行时原有撤信策略原样保留。
构建产物自检验证 SSLContext 必须开启 CERT_REQUIRED 与 check_hostname。
