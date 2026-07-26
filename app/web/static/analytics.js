(function(){
    'use strict';
    function setupDateRange(){
        var btns=document.querySelectorAll('.dm-range-btn');
        btns.forEach(function(btn){
            btn.addEventListener('click',function(){
                btns.forEach(function(b){b.classList.remove('active');});
                btn.classList.add('active');
            });
        });
    }
    if(document.readyState==='loading'){document.addEventListener('DOMContentLoaded',setupDateRange);}
    else{setupDateRange();}
}());
