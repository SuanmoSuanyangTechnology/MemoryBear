const { execSync } = await import('child_process');
// 企业私有配置（自行替换）
const PRIVATE_PACKAGE = '@redbear/memory-brick';
const PRIVATE_REGISTRY = 'http://10.206.16.48:4873';

console.log('🔍 检测内网环境，校验私有模块权限...');
try {
  // 超时检测：3秒内无法连通判定为外网环境
  execSync(`npm view ${PRIVATE_PACKAGE} --registry=${PRIVATE_REGISTRY}`, {
    stdio: 'ignore',
    timeout: 6000
  });
  // 内网环境：安装私有包
  console.log('✅ 识别公司内网环境，开始安装私有插件');
  execSync(`npm install ${PRIVATE_PACKAGE} --registry=${PRIVATE_REGISTRY}`, {
    stdio: 'inherit'
  });
  console.log('✅ 私有模块安装完成');
} catch (error) {
  // 外网环境：静默跳过，不抛出异常
  console.log('ℹ️ 公开外网环境，自动跳过私有模块安装', error);
}