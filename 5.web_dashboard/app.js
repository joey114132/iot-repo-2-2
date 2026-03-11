const API_BASE_URL = 'http://127.0.0.1:8000';

// Global state
let currentSection = 'overview';
let autoRefreshTimer = null;

// Initialize
document.addEventListener('DOMContentLoaded', () => {
    setupNavigation();
    setupGateControls();
    setupOperationMode();
    
    // Initial data fetch
    refreshAll();
    
    // Start auto-refresh
    autoRefreshTimer = setInterval(refreshAll, 1000);
});

// ─── Navigation ───
function setupNavigation() {
    const navItems = document.querySelectorAll('.nav-links li');
    navItems.forEach(item => {
        item.addEventListener('click', () => {
            const section = item.getAttribute('data-section');
            if (!section) return;
            
            // UI update
            navItems.forEach(i => i.classList.remove('active'));
            item.classList.add('active');
            
            // Section visibility
            document.querySelectorAll('.section').forEach(s => s.classList.remove('active'));
            document.getElementById(section).classList.add('active');
            
            currentSection = section;
            
            // Special handling for history
            if (section === 'history') {
                fetchHistory();
            } else if (section === 'residents') {
                fetchResidents();
            }
        });
    });
}

// ─── Data Refresh ───
async function refreshAll() {
    try {
        const dashboardData = await fetchAPI('/parking/dashboard');
        const devicesData = await fetchAPI('/devices/');
        
        updateSummary(dashboardData);
        updateDevicesSummary(devicesData);
        updateEvents(dashboardData.recent_events);
        updateGateUI(dashboardData);
        
        if (currentSection === 'parking-map') {
            updateParkingMap(dashboardData.slots);
        } else if (currentSection === 'devices') {
            updateDevicesGrid(devicesData);
        }
        
    } catch (error) {
        console.error('Refresh failed:', error);
    }
}

// ─── Component Updates ───
function updateSummary(data) {
    document.getElementById('stat-total-slots').textContent = data.total_slots || 0;
    document.getElementById('stat-occupied-slots').textContent = data.occupied_slots || 0;
    document.getElementById('stat-free-slots').textContent = data.free_slots || 0;
    document.getElementById('stat-active-devices').textContent = data.active_devices || 0;
}

function updateGateUI(data) {
    const indicator = document.getElementById('gate-indicator');
    const statusText = document.getElementById('current-gate-status');
    const autoBtn = document.getElementById('gate-auto-btn');
    
    // 0:연결안됨, 1:닫힘, 2:열림, 3:자동
    const state = data.gate_sensor_state;
    
    indicator.classList.remove('open', 'closed');
    
    if (state === 2) {
        indicator.classList.add('open');
        statusText.textContent = '열림';
    } else if (state === 1) {
        indicator.classList.add('closed');
        statusText.textContent = '닫힘';
    } else if (state === 3) {
        statusText.textContent = '자동';
    } else {
        statusText.textContent = '미연결';
    }
    
    if (state === 3) autoBtn.classList.add('active');
    else autoBtn.classList.remove('active');
}

function updateEvents(events) {
    const list = document.getElementById('recent-events-list');
    list.innerHTML = '';
    
    if (!events || events.length === 0) {
        list.innerHTML = '<li class="event-item">최근 이벤트가 없습니다.</li>';
        return;
    }
    
    events.slice(0, 5).forEach(ev => {
        const li = document.createElement('li');
        li.className = 'event-item';
        
        const typeClass = ev.event_type.toLowerCase();
        const time = new Date(ev.created_at).toLocaleTimeString();
        
        li.innerHTML = `
            <div class="event-time">${time}</div>
            <div class="event-content">
                <span class="event-tag ${typeClass}">${ev.event_type}</span>
                ${ev.message}
            </div>
        `;
        list.appendChild(li);
    });
}

function updateParkingMap(slots) {
    const streetGrid = document.getElementById('street-parking');
    const towerGrid = document.getElementById('tower-parking');
    
    streetGrid.innerHTML = '';
    towerGrid.innerHTML = '';
    
    slots.forEach(slot => {
        const div = document.createElement('div');
        div.className = `slot-box ${slot.is_occupied ? 'occupied' : ''} ${!slot.sensor_connected ? 'disconnected' : ''}`;
        
        div.innerHTML = `
            <span class="slot-name">${slot.name}</span>
            <span class="slot-status">
                ${!slot.sensor_connected ? 'OFF' : (slot.is_occupied ? '점유' : '여유')}
            </span>
        `;
        
        if (slot.name.startsWith('S')) streetGrid.appendChild(div);
        else if (slot.name.startsWith('T')) towerGrid.appendChild(div);
    });
}

// ─── API Fetches (History/Residents) ───
async function fetchHistory() {
    try {
        const records = await fetchAPI('/parking/records');
        const body = document.getElementById('history-body');
        body.innerHTML = '';
        
        records.forEach(rec => {
            const tr = document.createElement('tr');
            const entryTime = new Date(rec.entry_timestamp).toLocaleString();
            const exitTime = rec.exit_timestamp ? new Date(rec.exit_timestamp).toLocaleString() : '-';
            const status = rec.exit_timestamp ? '출차완료' : '주차중';
            
            tr.innerHTML = `
                <td>${rec.record_id}</td>
                <td><span class="plate-tag">${rec.license_plate}</span></td>
                <td>${entryTime}</td>
                <td>${exitTime}</td>
                <td style="color: ${rec.exit_timestamp ? 'var(--text-secondary)' : 'var(--accent-primary)'}">${status}</td>
                <td>${rec.charge_amount.toLocaleString()}원</td>
            `;
            body.appendChild(tr);
        });
    } catch (error) {
        console.error('History fetch failed:', error);
    }
}

async function fetchResidents() {
    try {
        const residents = await fetchAPI('/residents/');
        const body = document.getElementById('residents-body');
        body.innerHTML = '';
        
        residents.forEach(res => {
            const tr = document.createElement('tr');
            tr.innerHTML = `
                <td>${res.id}</td>
                <td>${res.unit_number}호</td>
                <td>${res.name}</td>
                <td>${res.phone}</td>
                <td><span class="plate-tag">${res.car_plate}</span></td>
                <td style="color: var(--accent-secondary)">${res.balance.toLocaleString()}원</td>
                <td><button class="control-btn" onclick="alert('관리 기능 준비 중')">수정</button></td>
            `;
            body.appendChild(tr);
        });
    } catch (error) {
        console.error('Residents fetch failed:', error);
    }
}

function updateDevicesGrid(devices) {
    const grid = document.querySelector('.devices-grid');
    grid.innerHTML = '';
    
    // In style.css we need to define devices-grid. I'll add a simple layout here via JS for now.
    grid.style.display = 'grid';
    grid.style.gridTemplateColumns = 'repeat(auto-fill, minmax(300px, 1fr))';
    grid.style.gap = '24px';

    devices.forEach(dev => {
        const card = document.createElement('div');
        card.className = 'card';
        card.style.display = 'flex';
        card.style.flexDirection = 'column';
        card.style.gap = '12px';
        
        card.innerHTML = `
            <div style="display: flex; justify-content: space-between; align-items: center">
                <h4 style="font-size: 18px">${dev.name}</h4>
                <span class="indicator ${dev.is_connected ? 'online' : ''}" style="background-color: ${dev.is_connected ? 'var(--success)' : 'var(--danger)'}"></span>
            </div>
            <div style="font-size: 13px; color: var(--text-secondary)">
                <p>Type: ${dev.type}</p>
                <p>IP: ${dev.ip_address || 'N/A'}</p>
                <p>GUID: ${dev.device_guid || 'N/A'}</p>
            </div>
            <div style="font-weight: 600; font-size: 14px; color: ${dev.is_connected ? 'var(--success)' : 'var(--danger)'}">
                ${dev.is_connected ? 'CONNECTED' : 'DISCONNECTED'}
            </div>
        `;
        grid.appendChild(card);
    });
}

// ─── Controls ───
function setupGateControls() {
    document.getElementById('gate-open-btn').addEventListener('click', () => sendGateCommand(2));
    document.getElementById('gate-close-btn').addEventListener('click', () => sendGateCommand(1));
    document.getElementById('gate-auto-btn').addEventListener('click', () => sendGateCommand(3));
}

async function sendGateCommand(state) {
    try {
        await postAPI(`/parking/gate-state?gate_sensor_state=${state}`);
        showToast(`차단기 명령 전송 성공: ${state}`);
        refreshAll();
    } catch (error) {
        showToast('차단기 명령 실패', true);
    }
}

function setupOperationMode() {
    const btn = document.getElementById('op-mode-btn');
    btn.addEventListener('click', async () => {
        const isCurrentlyOn = btn.classList.contains('on');
        const targetState = !isCurrentlyOn;
        try {
            await postAPI(`/parking/operation-mode?on=${targetState}`);
            showToast(`운영 상태 변경: ${targetState ? 'ON' : 'OFF'}`);
            refreshAll();
        } catch (error) {
            showToast('운영 상태 변경 실패', true);
        }
    });
}

// ─── Utilities ───
async function fetchAPI(endpoint) {
    const res = await fetch(`${API_BASE_URL}${endpoint}`);
    if (!res.ok) throw new Error(`HTTP error! status: ${res.status}`);
    return await res.json();
}

async function postAPI(endpoint) {
    const res = await fetch(`${API_BASE_URL}${endpoint}`, { method: 'POST' });
    if (!res.ok) throw new Error(`HTTP error! status: ${res.status}`);
    return await res.json();
}

function showToast(message, isError = false) {
    const toast = document.getElementById('toast');
    toast.textContent = message;
    toast.style.backgroundColor = isError ? 'var(--danger)' : 'var(--accent-primary)';
    toast.classList.add('show');
    setTimeout(() => toast.classList.remove('show'), 3000);
}
