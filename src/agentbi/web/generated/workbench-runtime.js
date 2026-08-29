"use strict";
var AgentBI;
(function (AgentBI) {
    async function request(url, options = {}) {
        const response = await fetch(url, { credentials: 'same-origin', ...options });
        const body = response.status === 204
            ? null
            : await response.json().catch(() => null);
        if (!response.ok) {
            const detail = typeof body === 'object' && body !== null && 'detail' in body
                ? String(body.detail || '')
                : '';
            throw new Error(detail || '请求失败，请稍后重试');
        }
        return body;
    }
    AgentBI.request = request;
    function parseSessionUser(value) {
        if (typeof value !== 'object' || value === null)
            throw new Error('登录身份响应无效');
        const user = value;
        const requiredStrings = [
            'subject', 'username', 'display_name', 'role', 'role_label', 'data_scope', 'csrf_token',
        ];
        if (requiredStrings.some(key => typeof user[key] !== 'string')) {
            throw new Error('登录身份响应缺少必要字段');
        }
        if (!Array.isArray(user.permissions) || user.permissions.some(item => typeof item !== 'string')) {
            throw new Error('登录权限响应无效');
        }
        return user;
    }
    class SessionClient {
        get user() { return this.activeUser; }
        accept(value) {
            this.activeUser = parseSessionUser(value);
            return this.activeUser;
        }
        clear() { this.activeUser = undefined; }
        async login(username, password) {
            const body = await request('/api/v1/auth/login', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ username, password }),
            });
            return this.accept(body.user);
        }
        async restore() {
            const body = await request('/api/v1/auth/me');
            return this.accept(body.user);
        }
        csrfHeaders(json = false) {
            if (!this.activeUser)
                throw new Error('登录会话已失效');
            return {
                ...(json ? { 'Content-Type': 'application/json' } : {}),
                'X-AgentBI-CSRF': this.activeUser.csrf_token,
            };
        }
        async logout() {
            if (!this.activeUser)
                return;
            try {
                await request('/api/v1/auth/logout', {
                    method: 'POST', headers: this.csrfHeaders(),
                });
            }
            finally {
                this.clear();
            }
        }
    }
    AgentBI.SessionClient = SessionClient;
    AgentBI.session = new SessionClient();
    AgentBI.drilldownRegistry = Object.freeze({
        version: 1,
        charts: {
            revenue: {
                metric: '销售收入', semanticModel: 'sales_model', sourceType: 'revenue',
                dimensions: ['区域', '产品线', '渠道'], pageTitle: '销售收入下钻分析',
                sourceTitle: '季度销售收入趋势', breadcrumb: '销售总览 › 2024 Q4 › 华东 › 产品线',
                connectorOne: '点击 2024 Q4，下钻维度：区域', levelOneLabel: '第 1 层 · 区域',
                levelOneTitle: '2024 Q4 各区域销售收入',
                bars: [['华东', 88, '420'], ['华南', 60, '280'], ['华北', 40, '190'], ['西南', 28, '130']],
                connectorTwo: '点击 华东，下钻维度：产品线', levelTwoLabel: '第 2 层 · 产品线',
                levelTwoTitle: '华东产品线贡献', tableDimension: '产品线', total: '420',
                rows: [['消费电子', '176', '42%'], ['家电', '126', '30%'], ['数码配件', '76', '18%'], ['其他', '42', '10%']],
                context: '第 2 层 · 华东产品线贡献', questions: ['华东增长来自哪里？', '哪个产品线拖累毛利？'],
                insight: '消费电子贡献 42%，但毛利率环比下降 3.2 个百分点。', insightSource: '洞察来源：与 2024 Q3 环比对比',
                evidence: '销售总览 → 2024 Q4 → 华东 → 产品线 → 渠道',
            },
            structure: {
                metric: '销售收入', semanticModel: 'sales_model', sourceType: 'structure',
                dimensions: ['区域', '渠道'], pageTitle: '销售结构下钻分析', sourceTitle: '销售结构（按产品大类）',
                breadcrumb: '销售总览 › 销售结构 › 消费电子 › 华东 › 渠道',
                connectorOne: '点击 消费电子，下钻维度：区域', levelOneLabel: '第 1 层 · 区域',
                levelOneTitle: '消费电子各区域销售收入',
                bars: [['华东', 88, '76'], ['华南', 63, '54'], ['华北', 41, '31'], ['西南', 24, '15']],
                connectorTwo: '点击 华东，下钻维度：渠道', levelTwoLabel: '第 2 层 · 渠道',
                levelTwoTitle: '华东消费电子渠道贡献', tableDimension: '渠道', total: '76',
                rows: [['直营门店', '31', '41%'], ['电商平台', '25', '33%'], ['经销商', '14', '18%'], ['其他', '6', '8%']],
                context: '第 2 层 · 华东消费电子渠道贡献', questions: ['消费电子主要由哪个区域贡献？', '哪个渠道增长最快？'],
                insight: '华东贡献消费电子收入的 43%，其中直营门店占华东渠道收入的 41%。',
                insightSource: '洞察来源：销售结构与渠道贡献交叉分析',
                evidence: '销售总览 → 销售结构 → 消费电子 → 华东 → 渠道',
            },
        },
    });
})(AgentBI || (AgentBI = {}));
