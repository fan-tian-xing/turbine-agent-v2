"""Configured text and pixel transport with one deadline across all providers."""
from __future__ import annotations

import base64
from copy import copy
import inspect
import json
import mimetypes
import queue
import threading
import time
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from turbine_kg.runtime import Budget, BudgetExceeded
from turbine_kg.settings import Settings, PROJECT_ROOT


class ModelError(ValueError):
    pass


class ModelAuthenticationError(ModelError):
    pass


class ModelTransientError(ModelError):
    pass


class ModelQuotaError(ModelError):
    pass


def call_model(model,system,user,budget,*,reserve_seconds=0,**kwargs):
    """Pass a stage deadline on the same Budget, including compatible test doubles."""
    if budget.remaining()<=reserve_seconds:raise BudgetExceeded('当前阶段已达到截止时间，剩余时间留给回答核验')
    parameters=inspect.signature(model.call).parameters.values()
    if reserve_seconds and any(p.name=='reserve_seconds' or p.kind==inspect.Parameter.VAR_KEYWORD for p in parameters):
        kwargs['reserve_seconds']=reserve_seconds
    return model.call(system,user,budget,**kwargs)


def json_object(text: str) -> dict:
    text = text.strip()
    if text.startswith('```'):
        text = text.split('\n', 1)[1].rsplit('```', 1)[0]
    value = json.loads(text)
    if not isinstance(value, dict):
        raise ModelError('模型返回值不是JSON对象')
    return value


class ModelClient:
    PROVIDER_PREFIXES = ('llm', 'llm_fallback', 'llm_fallback_2')
    # Offline knowledge extraction keeps the exact requirement in text.
    # Optional numeric and condition projections can be added after source review.

    def __init__(self, settings: Settings):
        self.settings = settings
        config = json.loads((PROJECT_ROOT / 'config/agent.json').read_text(encoding='utf-8'))
        self.policy = config['transport']
        enabled = config.get('model_routing', {}).get('enabled_provider_prefixes', list(self.PROVIDER_PREFIXES))
        if not isinstance(enabled, list) or any(prefix not in self.PROVIDER_PREFIXES for prefix in enabled):
            raise ValueError('enabled_provider_prefixes须为已知提供商前缀列表')
        self.enabled_provider_prefixes = tuple(dict.fromkeys(enabled))
        stage_models = config.get('model_routing', {}).get('stage_models', {})
        if not isinstance(stage_models, dict):
            raise ValueError('stage_models须为阶段到提供商模型前缀的映射')
        self.stage_models = {}
        for purpose, mapping in stage_models.items():
            if not isinstance(purpose, str) or not purpose.strip() or not isinstance(mapping, dict):
                raise ValueError('stage_models阶段或模型映射无效')
            for provider_prefix, model_prefix in mapping.items():
                if provider_prefix not in self.PROVIDER_PREFIXES or model_prefix not in self.PROVIDER_PREFIXES:
                    raise ValueError('stage_models包含未注册的提供商或模型前缀')
                model_name = getattr(settings, model_prefix + '_model', None)
                if not isinstance(model_name, str) or not model_name.strip():
                    raise ValueError('stage_models引用的模型前缀尚未配置：' + model_prefix)
            self.stage_models[purpose] = dict(mapping)
        self.stage_model_overrides = {}
        self.execution_policy = {'model_routing': {
            'enabled_provider_prefixes': list(self.enabled_provider_prefixes),
            'stage_models': self.stage_models}}
        # Keep the complete inventory for source fingerprints; routing only
        # determines which configured providers may receive a request.
        self.providers = []
        self.provider_prefixes = []
        for prefix in self.PROVIDER_PREFIXES:
            values = [getattr(settings, prefix + suffix) for suffix in ('_base_url', '_model', '_api_key', '_timeout_seconds')]
            if all(values[:3]):
                self.providers.append(values)
                self.provider_prefixes.append(prefix)
        self.vision_capability: dict[tuple[str, str], bool] = {}
        self.last_summary: dict = {}

    def _routed_providers(self):
        # Older transport doubles provide their complete request list directly.
        if not hasattr(self, 'enabled_provider_prefixes'):
            return self.providers
        overrides = getattr(self, 'stage_model_overrides', {})
        return [[endpoint, overrides.get(prefix, model), credential, timeout]
                for prefix, (endpoint, model, credential, timeout) in zip(self.provider_prefixes, self.providers)
                if prefix in self.enabled_provider_prefixes]

    def for_stage(self, purpose: str):
        """Select configured model names without changing endpoint inventory.

        Views share transport settings and capability cache; callers still pass
        their original Budget. Each model request uses the shared budget; claim binding is
        checked locally after generation.
        """
        if not isinstance(purpose, str) or not purpose.strip():
            raise ValueError('模型阶段须为非空名称')
        view = copy(self)
        view.stage_model_overrides = {}
        for provider_prefix, model_prefix in getattr(self, 'stage_models', {}).get(purpose, {}).items():
            if provider_prefix not in self.PROVIDER_PREFIXES or model_prefix not in self.PROVIDER_PREFIXES:
                raise ValueError('stage_models包含未注册的提供商或模型前缀')
            model_name = getattr(self.settings, model_prefix + '_model', None)
            if not isinstance(model_name, str) or not model_name.strip():
                raise ValueError('stage_models引用的模型前缀尚未配置：' + model_prefix)
            view.stage_model_overrides[provider_prefix] = model_name
        view.last_summary = {}
        return view

    @staticmethod
    def _completion(response):
        if not isinstance(response,dict): raise ModelError('模型API响应不是对象')
        error=response.get('error')
        if error:
            raw_error=error if isinstance(error,str) else ''
            error=error if isinstance(error,dict) else {}
            code=error.get('code');kind=error.get('type');status=error.get('status')
            message=' '.join(str(error.get(key,'')) for key in ('message','detail','error'))+' '+raw_error
            if code in ('invalid_api_key','unauthorized','access_denied') or kind=='authentication_error' or status in (401,403):
                raise ModelAuthenticationError('模型鉴权失败，未切换提供商')
            transient_phrases=('routing information','temporarily unavailable','service unavailable','upstream','gateway timeout','try again')
            if (code in ('rate_limit_exceeded','overloaded_error') or kind=='rate_limit_error' or
                    status in (429,500,502,503,504) or any(phrase in message.casefold() for phrase in transient_phrases)):
                raise ModelTransientError('模型API暂时不可用')
            if code == 'insufficient_quota' or status == 402:
                raise ModelQuotaError('模型额度不足，未切换提供商')
            known={'insufficient_quota','context_length_exceeded','invalid_request_error','model_not_found'}
            raise ModelError('模型API返回错误对象'+('：'+code if isinstance(code,str) and code in known else ''))
        choices=response.get('choices')
        if not isinstance(choices,list) or not choices or not isinstance(choices[0],dict): raise ModelError('模型API响应缺少choices')
        message=choices[0].get('message')
        if not isinstance(message,dict) or not isinstance(message.get('content'),str): raise ModelError('模型API响应缺少文字content')
        return message['content']

    @staticmethod
    def _request(request, timeout):
        result = queue.Queue(maxsize=1)
        def work():
            try:
                with urlopen(request, timeout=timeout) as response: value = json.loads(response.read())
                result.put((True, value))
            except Exception as error: result.put((False, error))
        thread = threading.Thread(target=work, daemon=True, name='turbine-model')
        thread.start(); thread.join(timeout)
        if thread.is_alive(): raise TimeoutError('模型请求达到截止时间')
        ok, value = result.get_nowait()
        if not ok: raise value
        return value

    def call(self, system: str, user: str, budget: Budget, *, images: list[Path] | None = None, reserve_seconds: float = 0) -> dict:
        self.last_summary = {'pixels_sent': False, 'response_received': False}
        if not isinstance(reserve_seconds,(int,float)) or reserve_seconds<0:raise ValueError('reserve_seconds须为非负秒数')
        def stage_remaining():
            remaining=budget.remaining()-reserve_seconds
            if remaining<=0:raise BudgetExceeded('当前阶段已达到截止时间，剩余时间留给回答核验')
            return remaining
        stage_remaining()
        if not self.settings.llm_allow_evidence_send: raise ModelError('当前配置未允许发送资料内容')
        content: str | list = user
        if images:
            content = [{'type': 'text', 'text': user}]
            for path in images:
                encoded = base64.b64encode(path.read_bytes()).decode('ascii')
                mime=mimetypes.guess_type(str(getattr(path,'name','image.png')))[0]
                if mime not in ('image/png','image/jpeg','image/webp','image/gif'): raise ModelError('图片资源格式不受视觉传输支持')
                content.append({'type': 'image_url', 'image_url': {'url': 'data:'+mime+';base64,' + encoded}})
        errors = []
        for endpoint, model, credential, configured_timeout in self._routed_providers():
            endpoint = endpoint.rstrip('/')
            if not endpoint.endswith('/chat/completions'): endpoint += '/chat/completions'
            capability_key = (endpoint, model)
            if images and self.vision_capability.get(capability_key) is False: continue
            for attempt in range(self.policy['transient_attempts']):
                timeout = min(configured_timeout, stage_remaining())
                payload = {'model': model, 'temperature': 0, 'messages': [{'role': 'system', 'content': system}, {'role': 'user', 'content': content}]}
                request = Request(endpoint, data=json.dumps(payload, ensure_ascii=False).encode(), headers={'Authorization': 'Bearer ' + credential, 'Content-Type': 'application/json'}, method='POST')
                try:
                    self.last_summary.update(model=model, attempt=attempt + 1, pixels_sent=bool(images))
                    response = self._request(request, timeout)
                    self.last_summary['response_received'] = True
                    stage_remaining()
                    result = json_object(self._completion(response))
                    if images: self.vision_capability[capability_key] = True
                    self.last_summary = {'model': model, 'attempt': attempt + 1, 'pixels_sent': bool(images), 'response_received': True}
                    return result
                except BudgetExceeded: raise
                except ModelAuthenticationError: raise
                except ModelQuotaError: raise
                except ModelTransientError as error:
                    errors.append(str(error))
                except HTTPError as error:
                    errors.append('HTTP ' + str(error.code))
                    # Authentication errors are never an automatic fallback trigger.
                    if error.code in (401, 403): raise ModelAuthenticationError('模型鉴权失败（HTTP '+str(error.code)+'），未切换提供商') from None
                    if error.code == 402: raise ModelQuotaError('模型额度不足（HTTP 402），未切换提供商') from None
                    if images and error.code == 415: self.vision_capability[capability_key] = False
                    if error.code != 429 and error.code < 500: break
                except URLError:
                    # DNS, proxy and connection failures can be brief. Retry
                    # within the existing deadline and on the same provider.
                    errors.append('URLError')
                except ModelError as error:
                    errors.append(str(error));break
                except (ValueError, KeyError, IndexError, TimeoutError, OSError) as error:
                    errors.append(type(error).__name__)
                    break
                delay = min(self.policy['max_backoff_seconds'], self.policy['backoff_seconds'] * 2 ** attempt)
                if delay >= stage_remaining(): raise BudgetExceeded('重试等待超出当前阶段预算')
                time.sleep(delay)
        self.last_summary['failures'] = errors
        raise ModelError('配置的模型均未返回可用结果：' + ', '.join(errors or ['未配置可用模型']))
