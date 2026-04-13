import React, { useEffect, useState, useRef, useMemo } from 'react'
import { isBaselineAccountName } from '@/lib/baselineAccounts'
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card"
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select"
import { Button } from "@/components/ui/button"
import { getLatestTraceId, getAgentTrace, getTraceHistory, AgentTrace, AgentStep, TraceSummary } from '@/lib/api'
import { Bot, User, Terminal, AlertCircle, RefreshCcw, History, Clock, Brain } from 'lucide-react'
import {
  Sheet,
  SheetContent,
  SheetDescription,
  SheetHeader,
  SheetTitle,
  SheetTrigger,
} from "@/components/ui/sheet"

interface AgentStatusViewProps {
    accounts: any[]
}

export default function AgentStatusView({ accounts }: AgentStatusViewProps) {
    const agentTraceAccounts = useMemo(
        () => (accounts || []).filter((a) => !isBaselineAccountName(a?.name)),
        [accounts]
    )

    const [selectedAccountId, setSelectedAccountId] = useState<string | null>(null)
    const [traceId, setTraceId] = useState<string | null>(null)
    const [trace, setTrace] = useState<AgentTrace | null>(null)
    const [history, setHistory] = useState<TraceSummary[]>([])
    const [isLiveMode, setIsLiveMode] = useState(true)
    const scrollRef = useRef<HTMLDivElement>(null)
    const [sheetOpen, setSheetOpen] = useState(false)

    // Baseline accounts have no agent traces; only list LLM/agent accounts.
    useEffect(() => {
        if (agentTraceAccounts.length === 0) {
            setSelectedAccountId(null)
            setTraceId(null)
            setTrace(null)
            return
        }
        setSelectedAccountId((prev) => {
            if (prev && agentTraceAccounts.some((a) => a.id.toString() === prev)) return prev
            return agentTraceAccounts[0].id.toString()
        })
    }, [agentTraceAccounts])

    // Poll for latest trace ID (only in live mode)
    useEffect(() => {
        if (!selectedAccountId || !isLiveMode) return

        const pollLatest = async () => {
            try {
                const res = await getLatestTraceId(parseInt(selectedAccountId))
                if (res.trace_id && res.trace_id !== traceId) {
                    setTraceId(res.trace_id)
                }
            } catch (e) {
                console.error("Failed to poll latest trace", e)
            }
        }

        pollLatest()
        const interval = setInterval(pollLatest, 2000)
        return () => clearInterval(interval)
    }, [selectedAccountId, traceId, isLiveMode])

    // Fetch history when sheet opens
    useEffect(() => {
        if (sheetOpen && selectedAccountId) {
            getTraceHistory(parseInt(selectedAccountId)).then(setHistory).catch(console.error)
        }
    }, [sheetOpen, selectedAccountId])

    // Poll for trace details if we have a traceId
    useEffect(() => {
        if (!traceId) return

        const fetchTrace = async () => {
            try {
                const data = await getAgentTrace(traceId)
                setTrace(data)
            } catch (e) {
                console.error("Failed to fetch trace details", e)
            }
        }

        fetchTrace()
        // Only poll if in live mode
        if (isLiveMode) {
            const interval = setInterval(fetchTrace, 2000)
            return () => clearInterval(interval)
        }
    }, [traceId, isLiveMode])

    // Auto-scroll
    useEffect(() => {
        if (scrollRef.current) {
            scrollRef.current.scrollTop = scrollRef.current.scrollHeight
        }
    }, [trace?.steps.length])

    const handleHistorySelect = (selectedTraceId: string) => {
        setTraceId(selectedTraceId)
        setIsLiveMode(false)
        setSheetOpen(false)
    }

    const handleGoLive = () => {
        setIsLiveMode(true)
        // Trigger a poll immediately
        if (selectedAccountId) {
            getLatestTraceId(parseInt(selectedAccountId)).then(res => {
                if (res.trace_id) setTraceId(res.trace_id)
            })
        }
    }

    const normalizeToolCalls = (toolCalls: any): any[] => {
        if (!toolCalls) return []
        if (!Array.isArray(toolCalls)) return [toolCalls]

        return toolCalls.map((item) => {
            if (typeof item === 'string') {
                // Backward compatibility for old traces where dicts were stringified.
                const nameMatch = item.match(/'name':\s*'([^']+)'/)
                const argsMatch = item.match(/'arguments':\s*'([^']*)'/)
                if (nameMatch || argsMatch) {
                    return {
                        function: {
                            name: nameMatch?.[1] || 'unknown',
                            arguments: argsMatch?.[1] || '{}',
                        },
                    }
                }
                return item
            }
            return item
        })
    }

    const extractThinkContent = (content: string | null | undefined): { think: string; main: string } => {
        const raw = content || ''
        const thinkRegex = /<think>([\s\S]*?)<\/think>/gi
        let thinkParts: string[] = []
        let match: RegExpExecArray | null
        while ((match = thinkRegex.exec(raw)) !== null) {
            const part = (match[1] || '').trim()
            if (part) thinkParts.push(part)
        }
        const main = raw.replace(thinkRegex, '').trim()
        return {
            think: thinkParts.join('\n\n'),
            main,
        }
    }

    const renderStep = (step: AgentStep) => {
        if (step.role === 'memory') {
            return (
                <div key={step.step_number} className="flex flex-col mb-4 items-center">
                    <div className="flex items-center gap-2 mb-1">
                        <div className="w-8 h-8 rounded-full flex items-center justify-center bg-orange-500 text-white">
                            <Brain size={16} />
                        </div>
                        <span className="text-xs text-muted-foreground">MEMORY</span>
                        <span className="text-xs text-muted-foreground">{new Date(step.created_at).toLocaleTimeString()}</span>
                    </div>
                    <div className="max-w-[80%] rounded-lg p-3 bg-orange-50 border border-orange-200 text-xs text-muted-foreground whitespace-pre-wrap break-all [overflow-wrap:anywhere]">
                        {step.content}
                    </div>
                </div>
            )
        }

        if (step.role === 'system') return null
        
        const isUser = step.role === 'user'
        const isTool = step.role === 'tool'
        
        // Check for agent name in content (Multi-Agent)
        let displayContent = step.content
        let agentName = null
        if (step.content && step.content.startsWith('[')) {
            const match = step.content.match(/^\[(.*?)\]/)
            if (match) {
                agentName = match[1]
                displayContent = step.content.substring(match[0].length).trim()
            }
        }
        const { think: thinkContent, main: mainContent } = extractThinkContent(displayContent || '')
        const normalizedToolCalls = normalizeToolCalls(step.tool_calls)

        return (
            <div key={step.step_number} className={`flex flex-col mb-4 ${isUser ? 'items-end' : 'items-start'}`}>
                <div className={`flex items-center gap-2 mb-1 ${isUser ? 'flex-row-reverse' : ''}`}>
                    <div className={`w-8 h-8 rounded-full flex items-center justify-center ${
                        isUser ? 'bg-blue-500 text-white' : 
                        isTool ? 'bg-purple-500 text-white' : 
                        agentName === 'Manager' ? 'bg-indigo-600 text-white' :
                        agentName === 'TradingAgent' ? 'bg-emerald-600 text-white' :
                        agentName === 'NewsAgent' ? 'bg-amber-500 text-white' :
                        agentName === 'CoderAgent' ? 'bg-pink-600 text-white' :
                        'bg-green-500 text-white'
                    }`}>
                        {isUser ? <User size={16} /> : 
                         isTool ? <Terminal size={16} /> : 
                         <Bot size={16} />}
                    </div>
                    <span className="text-xs text-muted-foreground">{step.role.toUpperCase()}</span>
                    {agentName && (
                         <span className={`text-xs px-1.5 py-0.5 rounded-full text-white ${
                            agentName === 'Manager' ? 'bg-indigo-600' :
                            agentName === 'TradingAgent' ? 'bg-emerald-600' :
                            agentName === 'NewsAgent' ? 'bg-amber-500' :
                            agentName === 'CoderAgent' ? 'bg-pink-600' :
                            'bg-gray-500'
                         }`}>
                            {agentName}
                        </span>
                    )}
                    <span className="text-xs text-muted-foreground">{new Date(step.created_at).toLocaleTimeString()}</span>
                </div>
                
                <div className={`max-w-[80%] rounded-lg p-3 ${isUser ? 'bg-primary text-primary-foreground' : 'bg-muted border'} overflow-hidden break-all [overflow-wrap:anywhere]`}>
                    {mainContent && (
                        <div className="whitespace-pre-wrap text-sm break-all [overflow-wrap:anywhere]">
                            {mainContent}
                        </div>
                    )}

                    {thinkContent && (
                        <div className="mt-2 rounded border border-amber-200 bg-amber-50 p-2">
                            <div className="mb-1 text-xs font-bold text-amber-700">Think</div>
                            <pre className="whitespace-pre-wrap text-xs text-amber-900 break-all [overflow-wrap:anywhere]">
                                {thinkContent}
                            </pre>
                        </div>
                    )}
                    
                    {normalizedToolCalls.length > 0 && (
                        <div className="mt-2 bg-black/5 p-2 rounded text-xs font-mono overflow-x-auto whitespace-pre-wrap break-all [overflow-wrap:anywhere]">
                            <div className="font-bold text-purple-600 mb-1">Tool Calls:</div>
                            <pre className="whitespace-pre-wrap break-all [overflow-wrap:anywhere]">{JSON.stringify(normalizedToolCalls, null, 2)}</pre>
                        </div>
                    )}

                    {step.tool_output && typeof step.tool_output === 'object' && Array.isArray((step.tool_output as any).selected_tools) && (
                        <div className="mt-2 bg-black/5 p-2 rounded text-xs font-mono overflow-x-auto whitespace-pre-wrap break-all [overflow-wrap:anywhere]">
                            <div className="font-bold text-blue-600 mb-1">Selected Tools:</div>
                            <pre className="whitespace-pre-wrap break-all [overflow-wrap:anywhere]">{(step.tool_output as any).selected_tools.join(', ')}</pre>
                        </div>
                    )}

                    {step.tool_output && (
                         <div className="mt-2 bg-black/5 p-2 rounded text-xs font-mono overflow-x-auto whitespace-pre-wrap break-all [overflow-wrap:anywhere]">
                            <div className="font-bold text-blue-600 mb-1">Tool Output:</div>
                            <pre className="whitespace-pre-wrap break-all [overflow-wrap:anywhere]">{typeof step.tool_output === 'string' ? step.tool_output : JSON.stringify(step.tool_output, null, 2)}</pre>
                        </div>
                    )}
                </div>
            </div>
        )
    }

    return (
        <div className="h-full flex flex-col p-4 gap-4">
            <div className="flex items-center justify-between">
                <div className="flex items-center gap-4">
                    <h2 className="text-2xl font-bold flex items-center gap-2">
                        <Bot className="w-6 h-6" />
                        Agent Status
                    </h2>
                    
                    {!isLiveMode && (
                        <Button variant="outline" size="sm" onClick={handleGoLive} className="flex items-center gap-1 text-green-600 border-green-200 hover:bg-green-50">
                            <RefreshCcw size={14} />
                            Go Live
                        </Button>
                    )}
                </div>

                <div className="flex items-center gap-2">
                    <Sheet open={sheetOpen} onOpenChange={setSheetOpen}>
                        <SheetTrigger asChild>
                            <Button variant="outline" size="icon">
                                <History className="h-4 w-4" />
                            </Button>
                        </SheetTrigger>
                        <SheetContent>
                            <SheetHeader>
                                <SheetTitle>Decision History</SheetTitle>
                                <SheetDescription>
                                    Past AI trading decisions and reasoning.
                                </SheetDescription>
                            </SheetHeader>
                            <div className="mt-4 space-y-4 max-h-[80vh] overflow-y-auto">
                                {history.map((item) => (
                                    <div 
                                        key={item.trace_id} 
                                        className="p-3 border rounded-lg hover:bg-muted cursor-pointer transition-colors"
                                        onClick={() => handleHistorySelect(item.trace_id)}
                                    >
                                        <div className="flex justify-between items-start mb-1">
                                            <span className={`text-xs px-2 py-0.5 rounded font-medium ${
                                                item.operation === 'buy' ? 'bg-green-100 text-green-700' : 
                                                item.operation === 'sell' ? 'bg-red-100 text-red-700' : 
                                                'bg-gray-100 text-gray-700'
                                            }`}>
                                                {item.operation.toUpperCase()} {item.symbol || ''}
                                            </span>
                                            <span className="text-xs text-muted-foreground flex items-center gap-1">
                                                <Clock size={10} />
                                                {new Date(item.timestamp).toLocaleString()}
                                            </span>
                                        </div>
                                        <p className="text-xs text-muted-foreground line-clamp-2">
                                            {item.reason}
                                        </p>
                                    </div>
                                ))}
                                {history.length === 0 && (
                                    <p className="text-sm text-center text-muted-foreground py-4">No history available</p>
                                )}
                            </div>
                        </SheetContent>
                    </Sheet>

                    <div className="w-[200px]">
                        <Select value={selectedAccountId || ""} onValueChange={setSelectedAccountId}>
                            <SelectTrigger>
                                <SelectValue placeholder="Select Account" />
                            </SelectTrigger>
                            <SelectContent>
                                {agentTraceAccounts.map((acc) => (
                                    <SelectItem key={acc.id} value={acc.id.toString()}>
                                        {acc.name}
                                    </SelectItem>
                                ))}
                            </SelectContent>
                        </Select>
                    </div>
                </div>
            </div>

            <Card className="flex-1 overflow-hidden flex flex-col">
                <CardHeader className="border-b py-3">
                    <CardTitle className="text-sm font-medium flex items-center justify-between">
                        <div className="flex items-center gap-2">
                            <span>Session ID: {traceId ? traceId.slice(0, 8) + '...' : "Waiting..."}</span>
                            {isLiveMode ? (
                                <span className="text-xs bg-green-100 text-green-800 px-2 py-1 rounded-full animate-pulse flex items-center gap-1">
                                    <span className="w-1.5 h-1.5 bg-green-500 rounded-full"></span> Live
                                </span>
                            ) : (
                                <span className="text-xs bg-gray-100 text-gray-800 px-2 py-1 rounded-full flex items-center gap-1">
                                    <History size={10} /> Historical
                                </span>
                            )}
                        </div>
                    </CardTitle>
                </CardHeader>
                <CardContent className="flex-1 overflow-auto p-4 min-w-0" ref={scrollRef}>
                    {agentTraceAccounts.length === 0 ? (
                        <div className="h-full flex items-center justify-center text-muted-foreground text-sm text-center px-4">
                            Baseline 账号（buy_hold / grid）无 Agent Trace，请从上方选择其他账号。
                        </div>
                    ) : trace ? (
                        <div className="space-y-4 w-full min-w-0 max-w-full">
                            {trace.steps.map(renderStep)}
                        </div>
                    ) : (
                        <div className="h-full flex items-center justify-center text-muted-foreground flex-col gap-2">
                            <RefreshCcw className="w-8 h-8 animate-spin" />
                            <p>Waiting for agent trace...</p>
                        </div>
                    )}
                </CardContent>
            </Card>
        </div>
    )
}

