pipeline {
    agent any

    options { timestamps() }

    stages {

        stage('Checkout') {
            steps {
                checkout scm
            }
        }

        stage('Setup Python') {
            steps {
                sh '''
                python3 -V
                python3 -m venv .venv
                . .venv/bin/activate
                python -m pip install --upgrade pip
                pip install -r requirements.txt
                '''
            }
        }

        stage('Run Tests') {
            steps {
                sh '''
                . .venv/bin/activate
                pytest -q
                '''
            }
        }

        stage('Run App') {
            steps {
                sh '''
                . .venv/bin/activate
                nohup python app.py > app.log 2>&1 &
                '''
            }
        }

        stage('Print URL') {
            steps {
                echo '🚀 App is running at: http://localhost:5055'
            }
        }
    }

    post {
    always {
        cleanWs()
    }

    success {
        withCredentials([string(credentialsId: 'slack-webhook', variable: 'SLACK_URL')]) {
            sh '''
            curl -X POST -H "Content-type: application/json" \
            --data "{\"text\":\"✅ SUCCESS: Jenkins Build #${BUILD_NUMBER} 🚀\\n🔗 ${BUILD_URL}\\n🌐 https://smart-parking-system-1jj9.onrender.com/\"}" \
            $SLACK_URL
            '''
        }
    }

    failure {
        withCredentials([string(credentialsId: 'slack-webhook', variable: 'SLACK_URL')]) {
            sh '''
            curl -X POST -H "Content-type: application/json" \
            --data "{\"text\":\"❌ FAILURE: Jenkins Build #${BUILD_NUMBER} 🔥\\n🔗 ${BUILD_URL}\\n🌐 https://smart-parking-system-1jj9.onrender.com/\"}" \
            $SLACK_URL
            '''
        }
    }
}
