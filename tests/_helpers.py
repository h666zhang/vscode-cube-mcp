"""测试共享的模块级常量与替身。

为什么不用 conftest 的 fixture:这些测试是 unittest.TestCase,测试方法只接 self,
拿不到 fixture 参数 —— 共享的东西只能是普通模块,`from _helpers import ...`。
"""

import os


class _FakeProc:
    """模拟 Popen:communicate(timeout) 返回 (stdout, stderr),带 returncode;可配置超时异常。"""

    def __init__(self, returncode=0, stdout="", stderr="", timeout_error=None):
        self.returncode = returncode
        self._stdout = stdout
        self._stderr = stderr
        self._timeout_error = timeout_error

    def communicate(self, timeout=None):
        if self._timeout_error is not None:
            raise self._timeout_error
        return self._stdout, self._stderr


# 构造一个含多外设的最小 .ioc 文本(仿 6.18 格式)
SAMPLE_IOC = """#MicroXplorer Configuration settings - do not modify
File.Version=6
Mcu.IP0=NVIC
Mcu.IP1=RCC
Mcu.IP2=SYS
Mcu.IP3=TIM2
Mcu.IP4=TIM3
Mcu.IPNb=5
Mcu.Pin0=VP_SYS_VS_Systick
Mcu.Pin1=VP_TIM3_VS_ClockSourceINT
Mcu.PinsNb=2
NVIC.TIM3_IRQn=true\\:0\\:0\\:false\\:false\\:true\\:true\\:true\\:true
ProjectManager.functionlistsort=1-SystemClock_Config-RCC-false-HAL-false,2-MX_GPIO_Init-GPIO-false-HAL-true,3-MX_TIM2_Init-TIM2-false-HAL-true,4-MX_TIM3_Init-TIM3-false-HAL-true
TIM3.AutoReloadPreload=TIM_AUTORELOAD_PRELOAD_ENABLE
TIM3.CounterMode=TIM_COUNTERMODE_UP
TIM3.Period=9999
VP_TIM3_VS_ClockSourceINT.Mode=Internal
VP_TIM3_VS_ClockSourceINT.Signal=TIM3_VS_ClockSourceINT
board=custom
"""


# 薄种子 + TIM 内部时钟片段注入测试(0.4.0)
SEED_IOC = """#MicroXplorer Configuration settings - do not modify
File.Version=6
Mcu.CPN=STM32F103C8T6
Mcu.Family=STM32F1
Mcu.IP0=NVIC
Mcu.IP1=RCC
Mcu.IP2=SYS
Mcu.IPNb=3
Mcu.Name=STM32F103C(8-B)Tx
Mcu.Package=LQFP48
Mcu.Pin0=PA13
Mcu.Pin1=VP_SYS_VS_Systick
Mcu.PinsNb=2
Mcu.UserName=STM32F103C8Tx
MxCube.Version=6.18.0
MxDb.Version=DB.6.0.180
ProjectManager.functionlistsort=1-SystemClock_Config-RCC-false-HAL-false
board=custom
"""


ADC_C_SAMPLE = """\
#include "adc.h"

ADC_HandleTypeDef hadc1;

void MX_ADC1_Init(void)
{
  hadc1.Init.ScanConvMode = ADC_SCAN_ENABLE;
  hadc1.Init.ContinuousConvMode = DISABLE;
  if (hadc1.Init.NbrOfConversion != 5)
  {
    Error_Handler();
  }
}

void HAL_ADC_MspInit(ADC_HandleTypeDef* adcHandle)
{
  if (adcHandle->Instance == ADC1)
  {
    __HAL_RCC_ADC1_CLK_ENABLE();
  }
}
"""


# ---------------------------------------------------------------- 集成测试(默认跳过)
INTEGRATION = os.environ.get("ST_CUBEMX_RUN_INTEGRATION") == "1"


TEMPLATE_IOC = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "templates", "STM32F103C8T6.ioc")
