"""Fresh, content-independent evidence for movable inventory inspection windows.

Counts visible window chrome, not item names, pictures, or historical openings.
No capture, input, OCR service, or permanent inventory exemption.
"""
import cv2
import numpy as np
from pathlib import Path


class InventoryUI:
    _LUT13=np.minimum(255,np.floor(np.arange(256)*1.3)).astype(np.uint8)
    _LUT12=np.minimum(255,np.floor(np.arange(256)*1.2)).astype(np.uint8)
    _CROSS_REFS=[]
    for _t in (2, 3, 4):
        _ref = np.zeros((16, 16), dtype='uint8')
        cv2.line(_ref, (1, 1), (14, 14), 255, _t)
        cv2.line(_ref, (14, 1), (1, 14), 255, _t)
        _ref_f = _ref.astype('float32')/255.
        _CROSS_REFS.append((_ref_f, float(np.linalg.norm(_ref_f))))

    def __init__(self, assets=None):
        path=Path(assets or Path(__file__).parent/'assets')/'transfer'/'equipment_cn.png'
        self.equipment=cv2.imdecode(np.frombuffer(path.read_bytes(),np.uint8),0)

    def _equipment_panel(self, gray, edges, complete):
        evidence=dict(present=False,equipment_score=0.,divider_count=0,cells=0,rows=0,columns=0)
        if not complete:return evidence
        header=gray[:65,:230]
        if header.shape[0]<self.equipment.shape[0] or header.shape[1]<self.equipment.shape[1]:return evidence
        score=float(cv2.minMaxLoc(cv2.matchTemplate(header,self.equipment,cv2.TM_CCOEFF_NORMED))[1])
        evidence['equipment_score']=round(max(0.,score),3)
        if score<.75:return evidence
        height,width=gray.shape
        # A separator may break into several edges against a very bright world.
        # Measure aligned support across rows instead of demanding one contour.
        band=edges[round(height*.04):round(height*.86)]
        support=np.mean(cv2.dilate(band,np.ones((1,7),np.uint8))>0,axis=0)
        dividers=[]
        for x in range(round(width*.48),round(width*.80)):
            if support[x]>=.60 and (not dividers or x-dividers[-1]>=10):dividers.append(x)
        evidence['divider_count']=len(dividers)
        if not dividers:return evidence
        # Equipment slots form several rows/columns on the left of the split.
        # A lone header, a recording toast, or an arbitrary rectangular building
        # cannot establish this full interaction-page structure.
        slots=[];limit=max(dividers)
        for c in cv2.findContours(edges[:round(height*.88),:limit],cv2.RETR_LIST,cv2.CHAIN_APPROX_SIMPLE)[0]:
            x,y,w,h=cv2.boundingRect(c)
            if not (24<=w<=width*.19 and 24<=h<=height*.27 and y>=height*.07):continue
            if cv2.contourArea(c)/(w*h)<.65:continue
            cx,cy=x+w/2,y+h/2
            if not any(abs(cx-a)<10 and abs(cy-b)<10 for a,b in slots):slots.append((cx,cy))
        rows={round(y/45) for _,y in slots};columns={round(x/55) for x,_ in slots}
        evidence.update(cells=len(slots),rows=len(rows),columns=len(columns))
        evidence['present']=len(slots)>=5 and len(rows)>=2 and len(columns)>=2
        return evidence

    @classmethod
    def _cross(cls, gray):
        _, ink = cv2.threshold(gray, max(110., float(np.median(gray))+35.), 255, cv2.THRESH_BINARY)
        points = cv2.findNonZero(ink)
        if points is None:
            return 0.
        x, y, w, h = cv2.boundingRect(points)
        if min(w, h) < 3 or not .45 < w/h < 2.:
            return 0.
        image = cv2.resize(ink[y:y+h, x:x+w], (16, 16)).astype('float32')/255.
        norm_img = float(np.linalg.norm(image))
        best = 0.
        for ref, norm_ref in cls._CROSS_REFS:
            score = float(np.sum(image*ref)/(norm_img*norm_ref+1e-6))
            if score > best:
                best = score
        return best

    @staticmethod
    def _bottom_rail(gray):
        height, width = gray.shape
        edge = cv2.Canny(gray[round(height*.68):], 30, 70)
        boxes = []
        for contour in cv2.findContours(edge, cv2.RETR_LIST, cv2.CHAIN_APPROX_SIMPLE)[0]:
            x, y, w, h = cv2.boundingRect(contour)
            if 28 <= w <= 115 and 35 <= h <= 115 and .55 < w/h < 1.55:
                boxes.append((x, y, w, h))
        # Item art/text split some cell contours. Their lower boundaries and
        # repeated horizontal pitch survive better than complete equal boxes.
        checked = set()
        for _, row, _, cell_h in boxes:
            bottom = row+cell_h
            bucket = round(bottom/6)
            if bucket in checked:
                continue
            checked.add(bucket)
            group = sorted(b[0] for b in boxes if abs(b[1]+b[3]-bottom) <= 7)
            xs = []
            for x in group:
                if not xs or x-xs[-1] >= 10:
                    xs.append(x)
            if len(xs) < 6:
                continue
            pitches = set()
            for a, x in enumerate(xs):
                for other in xs[a+1:a+7]:
                    for gaps in (1, 2, 3):
                        pitch = round((other-x)/gaps)
                        if 35 <= pitch <= 110:
                            pitches.add(pitch)
            for origin in xs:
                for pitch in pitches:
                    slots = set()
                    for x in xs:
                        index = round((x-origin)/pitch)
                        if abs(x-origin-index*pitch) <= max(3., pitch*.065):
                            slots.add(index)
                    if len(slots) >= 6:
                        span = max(slots)-min(slots)+1
                        if span <= 16 and len(slots)/span >= .55 and (span-1)*pitch >= 250:
                            return len(slots)
        return 0

    def detect(self, frame, full_size, layout=None, return_score=0.):
        # Menu geometry is independent of the locked combat viewport. Always
        # re-evaluate visible evidence; a prior opening never grants immunity.
        fw,fh=full_size
        views=[]
        if fw/fh>1.85:
            margin=max(0,round((fw-fh*16/9)/2))
            views.append(dict(left=margin,right=fw-margin,top=0))
        if layout:views.append(dict(left=layout['left'],right=layout['right'],top=layout['top']))
        views.append(dict(left=0,right=fw,top=0))
        seen=set();best=None
        for view in views:
            key=tuple(view[k] for k in ('left','right','top'))
            if key in seen:continue
            seen.add(key)
            result=self._detect_view(frame,full_size,view,return_score)
            result['viewport']=view
            if best is None:best=result
            if result.get('present'):return result
        return best

    @staticmethod
    def _inventory_structure(gray,edges):
        # Language-independent conjunction: selected navigation tab, repeated
        # bottom shortcut cells, and a multi-row equipment grid. No single
        # rectangle/text/old inventory state can establish protection.
        result=dict(present=False,selected_tab=False,bottom_slots=0,grid_cells=0)
        if gray.shape[0]<700:return result
        mask=(gray[:48,:min(1100,gray.shape[1])]>170).astype('uint8')*255
        for c in cv2.findContours(mask,cv2.RETR_EXTERNAL,cv2.CHAIN_APPROX_SIMPLE)[0]:
            x,y,w,h=cv2.boundingRect(c)
            if 70<=w<=300 and 14<=h<=44 and cv2.contourArea(c)/(w*h)>=.60:
                result['selected_tab']=True;break
        if not result['selected_tab']:return result
        result['bottom_slots']=InventoryUI._bottom_rail(gray)
        if result['bottom_slots']<6:return result
        cells=[]
        for c in cv2.findContours(edges[55:min(930,gray.shape[0]),:min(1250,gray.shape[1])],cv2.RETR_LIST,cv2.CHAIN_APPROX_SIMPLE)[0]:
            x,y,w,h=cv2.boundingRect(c)
            if 26<=w<=240 and 28<=h<=290 and cv2.contourArea(c)/(w*h)>=.65:
                cx,cy=x+w/2,y+h/2
                if not any(abs(cx-a)<8 and abs(cy-b)<8 for a,b in cells):cells.append((cx,cy))
        result['grid_cells']=len(cells)
        result['present']=len(cells)>=5 and len({round(y/50) for x,y in cells})>=3 and len({round(x/50) for x,y in cells})>=2
        return result

    def _detect_view(self, frame, full_size, layout=None, return_score=0.):
        result = dict(observed=False, complete=False, present=False, popup_count=0,
                      popups=[], bottom_slots=0, policy='fresh_window_chrome_or_inventory_rail')
        if frame is None or frame.shape[0] < 80:
            return result
        fw, fh = full_size
        left, right, top = (layout['left'], layout['right'], layout['top']) if layout else (0, fw, 0)
        patch = frame[top:, left:right, :3]
        if patch.shape[0] < 50 or patch.shape[1] < 160:
            return result
        # Work in the same width-normalized HUD coordinates across resolutions.
        scale = max(1920/patch.shape[1],1080/max(1,fh-2*top))
        patch = cv2.resize(patch, None, fx=scale, fy=scale)
        gray = cv2.cvtColor(patch, cv2.COLOR_BGR2GRAY)
        result.update(observed=True, complete=frame.shape[0] >= fh-2)
        edges = cv2.Canny(gray, 20, 60)
        structure=self._inventory_structure(gray,edges) if result['complete'] else dict(present=False)
        result['structure']=structure
        if structure['present']:
            result.update(present=True,bottom_slots=structure['bottom_slots'])
            return result
        panel=self._equipment_panel(gray,edges,result['complete'])
        result['equipment_panel']=panel
        if panel['present']:
            result['present']=True
            return result
        r8=patch[:,:,2]
        if cv2.minMaxLoc(r8)[1]>65:
            b8,g8,_=cv2.split(patch)
            red=cv2.bitwise_and(cv2.compare(r8,65,cv2.CMP_GT),
                cv2.bitwise_and(cv2.compare(r8,cv2.LUT(g8,self._LUT13),cv2.CMP_GT),
                                cv2.compare(r8,cv2.LUT(b8,self._LUT12),cv2.CMP_GT)))
            count, _, stats, _ = cv2.connectedComponentsWithStats(red)
        else:
            count=1;stats=()
        hlines=vlines=None
        for x, y, w, h, area in stats[1:]:
            x, y, w, h, area = map(int, (x, y, w, h, area))
            if not (7 <= w <= 32 and 7 <= h <= 30 and .5 < w/h < 2.5 and area >= w*h*.3):
                continue
            cross = self._cross(gray[y:y+h, x:x+w])
            if cross < .65:
                continue
            if hlines is None:
                horizontal = cv2.morphologyEx(edges, cv2.MORPH_OPEN, np.ones((1, 40), dtype='uint8'))
                vertical = cv2.morphologyEx(edges, cv2.MORPH_OPEN, np.ones((30, 1), dtype='uint8'))
                hlines = [cv2.boundingRect(c) for c in cv2.findContours(horizontal, cv2.RETR_LIST, cv2.CHAIN_APPROX_SIMPLE)[0]]
                vlines = [cv2.boundingRect(c) for c in cv2.findContours(vertical, cv2.RETR_LIST, cv2.CHAIN_APPROX_SIMPLE)[0]]
            header = [line for line in hlines if 120 <= line[2] <= gray.shape[1] and
                      abs(line[0]+line[2]-(x+w)) < 24 and abs(line[1]-y) < 15]
            border = [line for line in vlines if abs(line[0]-(x+w)) <= 10 and
                      line[1] <= y+h+30 and line[1]+line[3] >= y+h+45]
            # Window chrome can be physically cut off by the viewport. Only
            # substitute an edge when the button is actually next to that edge;
            # never treat an arbitrary missing border as proof of a window.
            right_clipped = x+w >= gray.shape[1]-10
            if not border and right_clipped:
                border = [(gray.shape[1]-1, y+h, 1, 60)]
            top_clipped = y <= max(6, h*.5)
            if not border:
                continue
            right_edge = min(border, key=lambda line:abs(line[0]-(x+w)))[0]
            lefts = [line[0] for line in vlines if 120 <= right_edge-line[0] <= gray.shape[1] and
                     line[1] <= y+h+30 and line[1]+line[3] >= y+h+50]
            edge_strip = gray[max(0,y-3):min(gray.shape[0],y+h+80),:8]
            left_clipped = (120 <= right_edge <= gray.shape[1] and edge_strip.size > 0 and
                            float(np.mean(edge_strip < 85)) >= .55)
            if left_clipped:
                lefts.append(0)
            if not lefts:
                continue
            # Choose the closest plausible left border with title ink. Underlying
            # inventory panels may supply other borders, but never a missing X.
            for left_edge in sorted(lefts, reverse=True):
                title = gray[max(0,y-4):y+h+3, left_edge+5:max(left_edge+6,x-3)]
                if not title.size:
                    continue
                ink = (title > max(105., float(np.median(title))+35.)).astype('uint8')*255
                cc, _, cstats, _ = cv2.connectedComponentsWithStats(ink)
                glyphs = sum(1 for _, _, cw, ch, ca in cstats[1:] if 2 <= cw <= 60 and 4 <= ch <= 30 and ca >= 4)
                body = gray[y+h+4:min(gray.shape[0],y+h+120),left_edge+4:max(left_edge+5,right_edge-4)]
                if glyphs < 3 or not body.size or float(np.mean(body < 85)) < .40:
                    continue
                clipped = []
                if left_edge == 0:
                    clipped.append('left')
                if not header:
                    if cross < .80:
                        continue
                    if top_clipped:
                        top_strip = gray[:max(1,y+1),left_edge+4:max(left_edge+5,right_edge-4)]
                        if not top_strip.size or float(np.mean(top_strip < 85)) < .50:
                            continue
                        clipped.append('top')
                    elif left_edge == 0:
                        continue
                    # A low-contrast header may have no long Canny segment.
                    # Real left + right sides, title glyphs, a strong X and
                    # window interior together already establish its chrome.
                if right_clipped and right_edge == gray.shape[1]-1:
                    clipped.append('right')
                result['popups'].append(dict(box=[left_edge,max(0,y-4),right_edge,min(gray.shape[0],y+h+120)],
                                             close=[x,y,w,h],cross_score=round(cross,3),title_glyphs=glyphs,
                                             clipped_edges=clipped))
                break
        result['popup_count'] = len(result['popups'])
        if result['popup_count']:
            result['present'] = True
        elif result['complete'] and return_score >= .80:
            result['bottom_slots'] = self._bottom_rail(gray)
            result['present'] = result['bottom_slots'] >= 6
        return result
